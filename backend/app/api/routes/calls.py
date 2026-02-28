import json
import logging
import re
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Response, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import settings
from app.core.twiml import (
    gather_speech,
    hold_and_dial,
    hold_then_hangup,
    play_and_gather,
    play_and_hangup,
    say_and_gather,
    say_and_hangup,
    start_stream_and_gather,
)
from app.models.call import Call
from app.models.escalation import Escalation
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.intent import infer_request_type, infer_request_types
from app.services.notifications import flush_call_notifications, queue_task_notification
from app.services.notification_store import create_notification
from app.services.deepgram_stream import DeepgramRealtimeBridge
from app.services.context import (
    get_context,
    update_context,
    cleanup_context,
    increment_turn,
    should_end_demo,
    log_conversation_quality,
)
from app.services.tts import cache_audio, get_cached_audio, synthesize_tts
from app.services.llm import generate_controlled_reply

router = APIRouter()
logger = logging.getLogger(__name__)
CREATE_GENERAL_QUESTION_TASKS = False

EMERGENCY_PATTERN = re.compile(
    r"\b("
    r"chest\s*(pain|tightness|pressure)|"
    r"can't\s*breathe|cannot\s*breathe|can\s*not\s*breathe|"
    r"cannot\s*breath|can't\s*breath|trouble\s*breathing|"
    r"bleeding|unconscious|suicide|ambulance|emergency"
    r")\b",
    re.IGNORECASE,
)
HUMAN_REQUEST_PATTERN = re.compile(
    r"\b("
    r"human|real\s+person|live\s+agent|operator|representative|"
    r"speak\s+to\s+(?:a\s+)?(?:person|human|agent|staff)|"
    r"talk\s+to\s+(?:a\s+)?(?:person|human|agent|staff)"
    r")\b",
    re.IGNORECASE,
)
NAME_PATTERN = re.compile(
    r"\bmy\s+(?:[a-z]+\s+)?name is\s+([a-zA-Z][a-zA-Z\s'\-]{1,40}?)(?=\s+(?:and|i)\b|[.,]|$)",
    re.IGNORECASE,
)
NAME_PATTERN_ALT = re.compile(
    r"\bmy\s+(?:[a-z]+\s+)?name\s*(?:is)?\s+([a-zA-Z][a-zA-Z\s'\-]{1,40}?)(?=\s+(?:and|i)\b|[.,]|$)",
    re.IGNORECASE,
)
NAME_TITLES = {"mr", "mrs", "ms", "miss", "missus", "dr", "doctor"}
NAME_BLOCKLIST = {
    "my",
    "name",
    "is",
    "no",
    "thank",
    "you",
    "appointment",
    "callback",
    "check",
    "up",
    "the",
    "in",
    "evening",
    "morning",
    "tomorrow",
    "today",
    "and",
    "please",
    "call",
    "back",
    "staff",
    "for",
    "to",
    "verify",
}
DATE_TIME_PATTERN = re.compile(
    r"\b("
    r"today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"morning|afternoon|evening|"
    r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?"
    r")\b",
    re.IGNORECASE,
)
VISIT_REASON_PATTERN = re.compile(r"\b(checkup|follow up|consultation|pain|fever|cough|injury)\b", re.IGNORECASE)
APPOINTMENT_TYPE_PATTERN = re.compile(
    r"\b("
    r"general\s*check(?:\s|-)?up|check(?:\s|-)?up|physical(?:\s*exam)?|"
    r"doctor(?:'s)?\s*visit|consultation|lab\s*test|blood\s*test|vaccination|follow(?:\s|-)?up"
    r")\b",
    re.IGNORECASE,
)
AMBIGUOUS_CLINIC_PATTERN = re.compile(
    r"\b("
    r"clinic\s+(car|cars|card|cards|arts)|"
    r"cleaning\s+cars|"
    r"clinic\s+cleaning\s+cars"
    r")\b",
    re.IGNORECASE,
)
APPOINTMENT_STATUS_PATTERN = re.compile(
    r"\b("
    r"appointment|booked|scheduled|schedule|at\s+\d|time"
    r")\b",
    re.IGNORECASE,
)
CALLBACK_STATUS_PATTERN = re.compile(
    r"\b("
    r"call\s*back|callback|staff\s+call|call\s+me"
    r")\b",
    re.IGNORECASE,
)
STATUS_QUESTION_PATTERN = re.compile(
    r"\b("
    r"is|did|have|can|confirm|status|check|"
    r"appointment\??|callback\??"
    r")\b",
    re.IGNORECASE,
)
CALLBACK_WINDOW_PATTERN = re.compile(
    r"\b(morning|afternoon|evening|\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\b",
    re.IGNORECASE,
)




def _normalized_stt_mode(mode: str | None) -> str:
    candidate = (mode or settings.stt_provider or "deepgram").strip().lower()

    if candidate == "deepgram":
        return "deepgram"

    if candidate == "twilio":
        return "twilio"

    return "deepgram"


def _use_direct_deepgram_streaming() -> bool:
    provider = (settings.stt_provider or "").strip().lower()
    return provider in {"deepgram_direct", "deepgram_ws"}


def _collect_callback_url(stt_mode: str) -> str:
    return f"{settings.public_base_url}/api/calls/collect?stt_mode={stt_mode}"


def _stream_ws_url(call_sid: str) -> str:
    base = settings.public_base_url.rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://") :]
    else:
        ws_base = base
    return f"{ws_base}/api/calls/stream?call_sid={call_sid}"


def _extract_appointment_details(text: str) -> dict[str, str]:
    details: dict[str, str] = {}

    def _clean_patient_name(raw: str) -> str | None:
        candidate = re.sub(r"[^A-Za-z\s'\-]", " ", raw).strip()
        candidate = re.sub(r"\s+", " ", candidate)
        if not candidate:
            return None
        parts = [p for p in candidate.split(" ") if p]
        if parts and parts[0].lower() in NAME_TITLES:
            parts = parts[1:]
        if not parts:
            return None
        lowered = [p.lower() for p in parts]
        if any(p in NAME_BLOCKLIST for p in lowered):
            return None
        # Prevent accidental capture of sentence fragments.
        if len(parts) > 3:
            return None
        if len(parts) == 1 and len(parts[0]) < 3:
            return None
        return " ".join(p.capitalize() for p in parts)

    name_match = NAME_PATTERN.search(text)
    if not name_match:
        name_match = NAME_PATTERN_ALT.search(text)
    if name_match:
        parsed = _clean_patient_name(name_match.group(1).strip().rstrip("."))
        if parsed:
            details["patient_name"] = parsed
    else:
        # Standalone likely-name utterance (e.g. "Rahul Singh")
        stripped = text.strip().strip(".")
        # Require at least two tokens to avoid treating random short words as names.
        if re.fullmatch(r"[A-Za-z]{2,}(?:\s+[A-Za-z]{2,}){1,2}", stripped):
            parsed = _clean_patient_name(stripped)
            if parsed:
                details["patient_name"] = parsed

    date_time = DATE_TIME_PATTERN.findall(text)
    if date_time:
        details["preferred_schedule"] = ", ".join(dict.fromkeys(item.strip() for item in date_time))

    reason_match = VISIT_REASON_PATTERN.search(text)
    if reason_match:
        details["visit_reason"] = reason_match.group(1).strip()
    appt_type_match = APPOINTMENT_TYPE_PATTERN.search(text)
    if appt_type_match:
        details["appointment_type"] = appt_type_match.group(1).strip()

    return details


def _callback_number_hint(callback_number: str | None) -> str:
    digits = "".join(ch for ch in (callback_number or "") if ch.isdigit())
    if len(digits) >= 4:
        return f"the number ending in {digits[-4:]}"
    if callback_number:
        return "your callback number on file"
    return "your number on file"


def _is_incomplete_transcript_fragment(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return True
    if t in {"no", "yes", "okay", "ok"}:
        return False
    # Common partial fragments from streaming STT that should not drive turn logic.
    bad_tails = (
        "my name is",
        "my full name is",
        "my phone name is",
        "my spelling name is",
        "name is",
    )
    if any(t.endswith(x) for x in bad_tails):
        return True
    return False


def _log_user_turn_quality(call_sid: str, current_text: str) -> None:
    log_conversation_quality(call_sid, current_text, "")


def _log_bot_turn_quality(call_sid: str, bot_text: str, user_text: str | None = None) -> None:
    log_conversation_quality(call_sid, user_text or "", bot_text)


def _normalize_transcript_text(text: str) -> str:
    normalized = text
    normalized = AMBIGUOUS_CLINIC_PATTERN.sub("clinic hours", normalized)
    normalized = re.sub(r"\bappoint\s+an?\s+appointment\b", "appointment", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _split_clauses(text: str) -> list[str]:
    parts = re.split(r"[.?!]|,|\balso\b|\band\b", text, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p and p.strip()]


def _intent_specific_text(call_sid: str, text: str, request_type: str, fallback_to_full: bool = True) -> str:
    clauses = _split_clauses(text)
    if not clauses:
        return text
    matched: list[str] = []
    for clause in clauses:
        if infer_request_type(call_sid,clause) == request_type:
            matched.append(clause)
    if matched:
        return ". ".join(matched)
    return text if fallback_to_full else ""


def _merge_extracted(existing: dict[str, str], incoming: dict[str, str]) -> dict[str, str]:
    merged = dict(existing)

    if "preferred_schedule" in incoming:
        old_vals = [v.strip() for v in str(merged.get("preferred_schedule", "")).split(",") if v.strip()]
        new_vals = [v.strip() for v in str(incoming.get("preferred_schedule", "")).split(",") if v.strip()]
        merged["preferred_schedule"] = ", ".join(dict.fromkeys(old_vals + new_vals))

    for key, value in incoming.items():
        if key == "preferred_schedule":
            continue
        merged[key] = value

    return merged


def _upsert_pending_appointment_details(db: Session, call_sid: str, transcript_text: str) -> Task | None:
    appt_task = (
        db.query(Task)
        .filter(
            Task.call_sid == call_sid,
            Task.status == "pending",
            Task.request_type == "appointment_scheduling",
        )
        .first()
    )
    if not appt_task:
        return None
    incoming = _extract_appointment_details(transcript_text)
    if not incoming:
        return appt_task
    details = _load_task_details(appt_task.details)
    extracted = details.get("extracted", {})
    if not isinstance(extracted, dict):
        extracted = {}
    extracted = _merge_extracted({k: str(v) for k, v in extracted.items() if isinstance(v, str)}, incoming)
    details["extracted"] = extracted
    appt_task.details = json.dumps(details)
    if incoming.get("patient_name"):
        appt_task.patient_name = incoming["patient_name"]
    return appt_task


def _escalation_reason(text: str, failed_turns: int) -> str | None:
    if EMERGENCY_PATTERN.search(text):
        return "medical_emergency_keyword"
    if HUMAN_REQUEST_PATTERN.search(text):
        return "requested_human"
    if failed_turns >= 3:
        return "failed_understanding_3_turns"
    return None


def _immediate_escalation_reason(text: str) -> str | None:
    if EMERGENCY_PATTERN.search(text):
        return "medical_emergency_keyword"
    if HUMAN_REQUEST_PATTERN.search(text):
        return "requested_human"
    return None


def _load_task_details(raw: str | None) -> dict[str, object]:
    try:
        parsed = json.loads(raw) if raw else {}
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return {"raw_request": raw or "", "extracted": {}}


def _get_extracted_from_task(task: Task | None) -> dict[str, str]:
    if not task:
        return {}
    details = _load_task_details(task.details)
    extracted = details.get("extracted", {})
    if not isinstance(extracted, dict):
        return {}
    return {k: str(v) for k, v in extracted.items() if isinstance(v, str)}


def _assigned_role_for_request(request_type: str, escalation_reason: str | None = None) -> str:
    if request_type == "escalation":
        if escalation_reason == "medical_emergency_keyword":
            return "doctor"
        return "staff"
    if request_type in {"medication_refill", "prescription_refill", "refill", "medical_question"}:
        return "doctor"
    return "staff"


def _get_or_create_transcript(db: Session, call_sid: str) -> Transcript:
    transcript = (
        db.query(Transcript)
        .filter(Transcript.call_sid == call_sid)
        .order_by(Transcript.id.desc())
        .first()
    )
    if transcript:
        return transcript
    transcript = Transcript(call_sid=call_sid, text="")
    db.add(transcript)
    return transcript


def _append_transcript_line(db: Session, call_sid: str, speaker: str, text: str) -> None:
    clean = text.strip()
    if not clean:
        return
    transcript = _get_or_create_transcript(db, call_sid)
    line = f"{speaker}: {clean}"
    transcript.text = f"{transcript.text}\n{line}".strip() if transcript.text else line


def _create_or_update_intent_task(
    db: Session,
    call_sid: str,
    from_number: str | None,
    request_type: str,
    transcript_text: str,
    extracted: dict[str, str],
) -> None:
    if request_type == "other":
        return
    if request_type == "general_question" and not CREATE_GENERAL_QUESTION_TASKS:
        return
    if request_type in {"other", "general_question"}:
        return

    matching_task = (
        db.query(Task)
        .filter(
            Task.call_sid == call_sid,
            Task.status == "pending",
            Task.request_type == request_type,
        )
        .first()
    )

    if not matching_task:
        task = Task(
            call_sid=call_sid,
            patient_name=extracted.get("patient_name"),
            callback_number=from_number,
            request_type=request_type,
            assigned_role=_assigned_role_for_request(request_type),
            priority="normal",
            details=json.dumps(
                {
                    "raw_request": transcript_text,
                    "notes": [transcript_text],
                    "extracted": extracted,
                }
            ),
        )
        db.add(task)
        db.flush()
        create_notification(
            db,
            role=task.assigned_role,
            title="New call task",
            message=f"Task #{task.id}: {task.request_type.replace('_', ' ')}",
            kind="task_created",
            is_urgent=(task.priority == "high"),
            call_sid=task.call_sid,
            task_id=task.id,
        )
        queue_task_notification(task)
        return

    details = _load_task_details(matching_task.details)
    notes = details.get("notes", [])
    if not isinstance(notes, list):
        notes = []
    notes.append(transcript_text)
    details["notes"] = notes[-10:]

    existing_extracted = details.get("extracted", {})
    if not isinstance(existing_extracted, dict):
        existing_extracted = {}
    if request_type == "appointment_scheduling":
        existing_extracted = _merge_extracted(existing_extracted, extracted)
        if extracted.get("patient_name"):
            matching_task.patient_name = extracted["patient_name"]
    details["extracted"] = existing_extracted
    matching_task.details = json.dumps(details)


def _create_escalation_task(
    db: Session,
    call_sid: str,
    from_number: str | None,
    escalation_reason: str,
    transcript_text: str,
) -> None:
    existing = (
        db.query(Task)
        .filter(
            Task.call_sid == call_sid,
            Task.status == "pending",
            Task.request_type == "escalation",
        )
        .first()
    )
    if existing:
        details = _load_task_details(existing.details)
        notes = details.get("notes", [])
        if not isinstance(notes, list):
            notes = []
        notes.append(transcript_text)
        details["notes"] = notes[-10:]
        details["escalation_reason"] = escalation_reason
        existing.details = json.dumps(details)
        return

    task = Task(
        call_sid=call_sid,
        patient_name=None,
        callback_number=from_number,
        request_type="escalation",
        assigned_role=_assigned_role_for_request("escalation", escalation_reason),
        priority="high",
        details=json.dumps(
            {
                "escalation_reason": escalation_reason,
                "notes": [transcript_text],
                "raw_request": transcript_text,
            }
        ),
    )
    db.add(task)
    db.flush()
    create_notification(
        db,
        role=task.assigned_role,
        title="Escalation task created",
        message=f"Task #{task.id}: {escalation_reason}",
        kind="escalation_task",
        is_urgent=True,
        call_sid=task.call_sid,
        task_id=task.id,
    )
    queue_task_notification(task, escalation_reason=escalation_reason)


def _transfer_target_number(escalation_reason: str) -> str | None:
    if escalation_reason == "medical_emergency_keyword":
        return settings.clinic_doctor_number or settings.clinic_staff_number
    return settings.clinic_staff_number


def _render_bot_reply_twiml(bot_message: str, callback_url: str, stt_mode: str) -> str:
    try:
        audio_bytes = synthesize_tts(bot_message)
        logger.warning("TTS BYTES LENGTH = %s", len(audio_bytes) if audio_bytes else 0)
    except Exception as exc:
        logger.error("TTS synthesis failed provider=%s error=%s", settings.tts_provider, str(exc))
        audio_bytes = None

    if audio_bytes:
        audio_id = cache_audio(audio_bytes)
        audio_url = f"{settings.public_base_url}/api/calls/tts/{audio_id}"

        logger.warning("TTS URL SENT TO TWILIO = %s", audio_url)

        return play_and_gather(audio_url=audio_url, action_url=callback_url, stt_mode=stt_mode)

    return say_and_gather(bot_message, callback_url, reprompt=None, stt_mode=stt_mode)


def _render_bot_hangup_twiml(bot_message: str) -> str:
    try:
        audio_bytes = synthesize_tts(bot_message)
    except Exception as exc:
        logger.error("TTS synthesis failed provider=%s error=%s", settings.tts_provider, str(exc))
        audio_bytes = None
    if audio_bytes:
        audio_id = cache_audio(audio_bytes)
        audio_url = f"{settings.public_base_url}/api/calls/tts/{audio_id}"
        return play_and_hangup(audio_url=audio_url)
    return say_and_hangup(bot_message)


def _appointment_reply(task: Task, callback_number: str | None) -> str:
    extracted = _get_extracted_from_task(task)
    has_name = bool((task.patient_name or "").strip() or extracted.get("patient_name"))
    schedule = extracted.get("preferred_schedule", "").strip()
    appointment_type = extracted.get("appointment_type", "").strip()
    if not has_name:
        return "Please tell me your full name so I can complete the appointment request."
    if not schedule:
        return "Please tell me your preferred date and time for the appointment."
    if not appointment_type:
        return "Please tell me what type of appointment you need, for example a general check-up or consultation."
    return (
        f"Thanks. I have your {appointment_type} appointment request for {schedule}. "
        f"Our clinic staff will call you at {_callback_number_hint(callback_number)} to confirm. "
        "Can I help you with anything else?"
    )


def _callback_window_from_task(task: Task | None) -> str | None:
    if not task:
        return None
    details = _load_task_details(task.details)
    notes = details.get("notes", [])
    if isinstance(notes, list):
        for note in reversed(notes):
            if not isinstance(note, str):
                continue
            match = CALLBACK_WINDOW_PATTERN.search(note)
            if match:
                return match.group(1)
    raw = details.get("raw_request")
    if isinstance(raw, str):
        match = CALLBACK_WINDOW_PATTERN.search(raw)
        if match:
            return match.group(1)
    return None



@router.post("/connect")
def connect_call(
    from_number: str | None = Form(None),
    to_number: str | None = Form(None),
    db: Session = Depends(get_db),
) -> dict:
    call_sid = uuid4().hex
    call = Call(call_sid=call_sid, from_number=from_number, to_number=to_number, status="connected")
    db.add(call)
    db.commit()
    return {"call_sid": call_sid, "status": "connected"}




@router.post("/webhook")
def twilio_webhook(
    CallSid: str = Form(...),
    From: str | None = Form(None),
    To: str | None = Form(None),
    CallStatus: str | None = Form(None),
    db: Session = Depends(get_db),
) -> Response:
    call = db.query(Call).filter(Call.call_sid == CallSid).first()
    ctx = get_context(CallSid)
    stt_mode = _normalized_stt_mode(settings.stt_provider)
    callback_url = _collect_callback_url(stt_mode)
    if not call:
        call = Call(call_sid=CallSid, from_number=From, to_number=To, status=CallStatus)
        db.add(call)
        db.commit() 
        stt_mode = _normalized_stt_mode(settings.stt_provider)
        update_context(
            CallSid,
            {
                "stt_mode": stt_mode,
                "stt_empty_turns": 0,
                "turn_count": 0,
                "appointment_confirmed": False,
            },
        )
        callback_url = _collect_callback_url(stt_mode)
        bot_message = "Thank you for calling. I am the clinic assistant. How may I help you today?"
        update_context(CallSid, {"state": "GENERAL"})
    else:
        # Twilio can retry /webhook callbacks; avoid restarting with full greeting.
        call.status = CallStatus or call.status
        db.commit()
        if (CallStatus or "").lower() in {"completed", "canceled", "busy", "failed", "no-answer"}:
            flush_call_notifications(CallSid)
            cleanup_context(CallSid)
        bot_message = "We are continuing your call. Please tell me how I can help."

    _append_transcript_line(db, CallSid, "BOT", bot_message)
    db.commit()
    _log_bot_turn_quality(CallSid, bot_message)
    logger.info("bot_reply call_sid=%s source=webhook text=%r", CallSid, bot_message)
    if _use_direct_deepgram_streaming() and settings.deepgram_api_key:
        twiml = start_stream_and_gather(
            message=bot_message,
            stream_url=_stream_ws_url(CallSid),
            action_url=callback_url,
            stt_mode="deepgram",
            reprompt=None,
        )
    else:
        twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=stt_mode)
    return Response(content=twiml, media_type="application/xml")


@router.post("/collect")
def collect_speech(
    CallSid: str = Form(...),
    From: str | None = Form(None),
    SpeechResult: str | None = Form(None),
    Confidence: float | None = Form(None),
    stt_mode: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    ctx = get_context(CallSid)

    #  ADDITION 1 — ensure stage always exists
    if not ctx.get("conversation_stage"):
        update_context(CallSid, {"conversation_stage": "COLLECTING"})
        ctx = get_context(CallSid)

    effective_stt_mode = _normalized_stt_mode(stt_mode or ctx.get("stt_mode"))
    update_context(CallSid, {"stt_mode": effective_stt_mode})
    raw_transcript_text = (SpeechResult or "").strip()

    if _use_direct_deepgram_streaming() and settings.ws_brain_mode:
        dg = dict(ctx.get("deepgram_transcripts") or {"final": "", "latest": "", "best": ""})
        deepgram_final = str(dg.get("final", "")).strip()
        deepgram_latest = str(dg.get("latest", "")).strip()
        deepgram_best = str(dg.get("best", "")).strip()
        update_context(CallSid, {"deepgram_transcripts": {"final": "", "latest": "", "best": ""}})
        if _is_incomplete_transcript_fragment(deepgram_final):
            deepgram_final = ""
        if _is_incomplete_transcript_fragment(deepgram_best):
            deepgram_best = ""
        if _is_incomplete_transcript_fragment(deepgram_latest):
            deepgram_latest = ""
        if deepgram_final:
            logger.warning("stt_turn source=deepgram_final transcript=%r", deepgram_final)
            raw_transcript_text = deepgram_final
        elif deepgram_best:
            logger.warning("stt_turn source=deepgram_best transcript=%r", deepgram_best)
            raw_transcript_text = deepgram_best
        elif deepgram_latest:
            logger.warning("stt_turn source=deepgram_latest transcript=%r", deepgram_latest)
            raw_transcript_text = deepgram_latest
        elif settings.ws_webhook_fallback:
            logger.warning("stt_turn source=twilio_fallback transcript=%r", raw_transcript_text)
        else:
            raw_transcript_text = ""
            logger.warning("stt_turn source=deepgram_only_no_text")

    callback_url = _collect_callback_url(effective_stt_mode)
    logger.info(
        "stt_turn call_sid=%s provider=%s confidence=%s transcript=%r",
        CallSid,
        effective_stt_mode,
        Confidence,
        raw_transcript_text,
    )

    low_confidence = Confidence is not None and Confidence < settings.stt_low_confidence_threshold
    if effective_stt_mode == "deepgram" and (not raw_transcript_text or low_confidence):
        update_context(CallSid, {"stt_empty_turns": int(ctx.get("stt_empty_turns", 0)) + 1})
    else:
        update_context(CallSid, {"stt_empty_turns": 0})
    if effective_stt_mode == "deepgram" and int(get_context(CallSid).get("stt_empty_turns", 0)) >= settings.stt_fallback_empty_turns:
        effective_stt_mode = "twilio"
        update_context(CallSid, {"stt_mode": effective_stt_mode, "stt_empty_turns": 0})
        callback_url = _collect_callback_url(effective_stt_mode)
        logger.warning("stt_fallback switched_to=twilio")

    if not raw_transcript_text:
        last_state = get_context(CallSid).get("state", "GENERAL")

        if last_state == "APPOINTMENT_NAME":
            bot_message = "I'm sorry, I didn't catch your name. Could you please repeat it?"
        elif last_state == "POST_TASK":
            bot_message = "I'm still here. Was there anything else you needed help with?"
        else:
            bot_message = "I'm sorry, I didn't hear anything. Could you please repeat your request?"
        update_context(CallSid, {
            "no_speech_count": int(ctx.get("no_speech_count", 0)) + 1
        })

        _append_transcript_line(db, CallSid, "BOT", bot_message)
        db.commit()
        _log_bot_turn_quality(CallSid, bot_message)

        twiml = _render_bot_reply_twiml(
            bot_message,
            callback_url,
            stt_mode=effective_stt_mode,
        )

        return Response(content=twiml, media_type="application/xml")

    update_context(CallSid, {"no_speech_count": 0})
    increment_turn(CallSid)
    _append_transcript_line(db, CallSid, "USER", raw_transcript_text)
    _log_user_turn_quality(CallSid, raw_transcript_text)

    transcript_text = _normalize_transcript_text(raw_transcript_text)

    ctx = get_context(CallSid)



    immediate_escalation = _immediate_escalation_reason(transcript_text)
    if immediate_escalation:
        esc = Escalation(call_sid=CallSid, reason=immediate_escalation, details=transcript_text)
        db.add(esc)
        db.flush()
        for role in ("staff", "doctor"):
            create_notification(
                db,
                role=role,
                title="Urgent escalation",
                message=f"{immediate_escalation} ({CallSid})",
                kind="escalation",
                is_urgent=True,
                call_sid=CallSid,
                escalation_id=esc.id,
            )
        _create_escalation_task(db, CallSid, From, immediate_escalation, transcript_text)
        bot_message = "I am escalating this call to our clinic staff right away for better support."
        _append_transcript_line(db, CallSid, "BOT", bot_message)
        db.commit()
        _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
        flush_call_notifications(CallSid)
        cleanup_context(CallSid)
        logger.info("bot_reply call_sid=%s source=immediate_escalation text=%r", CallSid, bot_message)
        # Generate TTS for escalation message
        try:
            audio_bytes = synthesize_tts(bot_message)
            audio_id = cache_audio(audio_bytes)
            message_audio_url = f"{settings.public_base_url}/api/calls/tts/{audio_id}"
            print(f"DEBUG ESCALATION AUDIO URL = {message_audio_url}") 
        except Exception as e:
            print(f"DEBUG ESCALATION TTS FAILED = {e}")
            message_audio_url = None
        transfer_target = _transfer_target_number(immediate_escalation)
        if transfer_target:
            twiml = hold_and_dial(
                message=bot_message,
                target_number=transfer_target,
                hold_music_url=settings.hold_music_url,
                message_audio_url=message_audio_url, 
            )
        else:
            twiml = hold_then_hangup(bot_message, hold_seconds=10)
        return Response(content=twiml, media_type="application/xml")

   
    # 1. First, identify what the user wants and extract data
    request_types = infer_request_types(CallSid, transcript_text)
    if request_types == ["other"]:
        update_context(CallSid, {
            "other_intent_turns": int(get_context(CallSid).get("other_intent_turns", 0)) + 1
        })
    else:
        update_context(CallSid, {"other_intent_turns": 0})

    if int(get_context(CallSid).get("other_intent_turns", 0)) >= 3:
        _create_escalation_task(db, CallSid, From, "failed_understanding_3_turns", transcript_text)
        db.commit()
        update_context(CallSid, {"other_intent_turns": 0})
        bot_message = "I'm having trouble understanding. Let me connect you to our staff."
        _append_transcript_line(db, CallSid, "BOT", bot_message)
        db.commit()
        try:
            audio_bytes = synthesize_tts(bot_message)
            audio_id = cache_audio(audio_bytes)
            message_audio_url = f"{settings.public_base_url}/api/calls/tts/{audio_id}"
        except Exception as e:
            message_audio_url = None
        flush_call_notifications(CallSid)
        cleanup_context(CallSid)
        transfer_target = settings.clinic_staff_number
        if transfer_target:
            twiml = hold_and_dial(
                message=bot_message,
                target_number=transfer_target,
                hold_music_url=settings.hold_music_url,
                message_audio_url=message_audio_url,
            )
        else:
            twiml = hold_then_hangup(bot_message, hold_seconds=10)
        return Response(content=twiml, media_type="application/xml")

    
    entities = {}
    extracted = _extract_appointment_details(transcript_text)

    if extracted.get("patient_name"):
        entities["name"] = extracted["patient_name"]
    if extracted.get("preferred_schedule"):
        entities["date"] = extracted["preferred_schedule"]
    if extracted.get("appointment_type"):
        entities["appointment_type"] = extracted["appointment_type"]
    if extracted.get("preferred_time"):
        entities["time"] = extracted["preferred_time"]

    # 2. Generate the LLM reply FIRST (This updates the state to POST_TASK if finished)
    llm_start = perf_counter()
    logger.info("STATE BEFORE LLM = %s", get_context(CallSid).get("state"))
    llm_reply, new_state, final_slots = generate_controlled_reply(CallSid, transcript_text)
    update_context(CallSid, {
        "state": new_state,
        "slots": final_slots, 
        "last_user_text": transcript_text
    })
    latency_ms = int((perf_counter() - llm_start) * 1000)


    # 3. NOW check if we should create the task in the database
    # We check the context AFTER the LLM/State Machine has processed the turn
    current_ctx = get_context(CallSid)
    if current_ctx.get("state") == "POST_TASK" and not current_ctx.get("task_created"):
        for request_type in request_types:
            _create_or_update_intent_task(
                db=db,
                call_sid=CallSid,
                from_number=From,
                request_type=request_type,
                transcript_text=transcript_text,
                extracted=entities, # Safe to use now!
            )
        update_context(CallSid, {"task_created": True})
    db.commit()

    if llm_reply.fallback_used and llm_reply.openai_status.endswith("_empty_output"):
        update_context(
        CallSid,
        {"failed_turns": int(get_context(CallSid).get("failed_turns", 0)) + 1},
    )
    else:
        update_context(CallSid, {"failed_turns": 0})
    if (
        llm_reply.openai_status.endswith("_request_exception")
        or llm_reply.openai_status.endswith("_empty_output")
        or "_http_" in llm_reply.openai_status
    ):
       update_context(
        CallSid,
        {"ai_failures": int(get_context(CallSid).get("ai_failures", 0)) + 1},
        )
    else:
        update_context(CallSid, {"ai_failures": 0})

    escalation_reason = _escalation_reason(transcript_text, int(get_context(CallSid).get("failed_turns", 0)))
    if escalation_reason == "failed_understanding_3_turns" and bool(get_context(CallSid).get("appointment_confirmed")):
        escalation_reason = None
    if escalation_reason:
        esc = Escalation(call_sid=CallSid, reason=escalation_reason, details=transcript_text)
        db.add(esc)
        db.flush()
        for role in ("staff", "doctor"):
            create_notification(
                db,
                role=role,
                title="Escalation detected",
                message=f"{escalation_reason} ({CallSid})",
                kind="escalation",
                is_urgent=True,
                call_sid=CallSid,
                escalation_id=esc.id,
            )
        if escalation_reason in {"medical_emergency_keyword", "requested_human"}:
            _create_escalation_task(db, CallSid, From, escalation_reason, transcript_text)
        db.commit()
        update_context(CallSid, {"failed_turns": 0, "ai_failures": 0})
        bot_message = "I am having trouble understanding. Let me connect you to our staff."
        _append_transcript_line(db, CallSid, "BOT", bot_message)
        db.commit()
        _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
        logger.info(
            "call_turn call_sid=%s transcript=%r openai_status=%s latency_ms=%s fallback=%s escalated=%s reason=%s",
            CallSid,
            transcript_text,
            llm_reply.openai_status,
            latency_ms,
            llm_reply.fallback_used,
            True,
            escalation_reason,
        )
        return Response(
            content=_render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode),
            media_type="application/xml",
        )

    if int(get_context(CallSid).get("ai_failures", 0)) >= 3:
        esc = Escalation(call_sid=CallSid, reason="ai_service_instability", details=transcript_text)
        db.add(esc)
        db.flush()
        for role in ("staff", "doctor"):
            create_notification(
                db,
                role=role,
                title="AI service instability",
                message=f"Call requires manual review ({CallSid})",
                kind="escalation",
                is_urgent=True,
                call_sid=CallSid,
                escalation_id=esc.id,
            )
        db.commit()
        update_context(CallSid, {"ai_failures": 0})
        bot_message = "I am having technical issues. Say human and I will connect staff."
        _append_transcript_line(db, CallSid, "BOT", bot_message)
        db.commit()
        _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
        return Response(
            content=_render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode),
            media_type="application/xml",
        )

    logger.info(
        "call_turn call_sid=%s transcript=%r openai_status=%s latency_ms=%s fallback=%s escalated=%s",
        CallSid,
        transcript_text,
        llm_reply.openai_status,
        latency_ms,
        llm_reply.fallback_used,
        False,
    )

    if get_context(CallSid).get("state") == "END_CALL":
        flush_call_notifications(CallSid)
        cleanup_context(CallSid)

        return Response(
            content=_render_bot_hangup_twiml(llm_reply.text),
            media_type="application/xml",
        )
    _append_transcript_line(db, CallSid, "BOT", llm_reply.text)
    db.commit()
    _log_bot_turn_quality(CallSid, llm_reply.text, raw_transcript_text)
    logger.info("bot_reply call_sid=%s source=llm text=%r", CallSid, llm_reply.text)
    return Response(
        content=_render_bot_reply_twiml(llm_reply.text, callback_url, stt_mode=effective_stt_mode),
        media_type="application/xml",
    )


@router.get("/tts/{audio_id}")
def tts_audio(audio_id: str) -> Response:
    audio = get_cached_audio(audio_id)
    if not audio:
        raise HTTPException(status_code=404, detail="Audio not found")
    return Response(content=audio, media_type="audio/mpeg")


@router.websocket("/stream")
async def twilio_media_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    call_sid_hint = websocket.query_params.get("call_sid")
    if not settings.deepgram_api_key:
        logger.warning("deepgram_stream disabled: missing DEEPGRAM_API_KEY")
        await websocket.close()
        return

    def _on_transcript(call_sid: str, transcript: str, is_final: bool) -> None:
        ctx = get_context(call_sid)
        dg = dict(ctx.get("deepgram_transcripts") or {"final": "", "latest": "", "best": ""})
        dg["latest"] = transcript
        prev_best = str(dg.get("best", ""))
        if len(transcript.strip()) >= len(prev_best.strip()):
            dg["best"] = transcript
        if is_final:
            dg["final"] = transcript
            logger.warning("deepgram_final transcript=%r", transcript)
        else:
            logger.warning("deepgram_partial transcript=%r", transcript)
        update_context(call_sid, {"deepgram_transcripts": dg})

    bridge = DeepgramRealtimeBridge(
        api_key=settings.deepgram_api_key,
        on_transcript=_on_transcript,
        call_sid_hint=call_sid_hint,
    )
    try:
        await bridge.connect()
        while True:
            raw = await websocket.receive_text()
            await bridge.ingest_twilio_message(raw)
    except WebSocketDisconnect:
        logger.info("twilio stream disconnected call_sid=%s", bridge.call_sid or call_sid_hint or "unknown")
    except Exception as exc:
        logger.error("twilio stream failed call_sid=%s error=%s", bridge.call_sid or call_sid_hint or "unknown", str(exc))
    finally:
        await bridge.close()
        sid = bridge.call_sid or call_sid_hint
        if sid:
            cleanup_context(sid)