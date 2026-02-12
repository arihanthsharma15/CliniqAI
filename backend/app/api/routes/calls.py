import json
import logging
import re
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Response
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
)
from app.models.call import Call
from app.models.escalation import Escalation
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.intent import infer_request_type, infer_request_types
from app.services.llm import generate_call_reply
from app.services.notifications import notify_task_created
from app.services.tts import cache_audio, get_cached_audio, synthesize_tts

router = APIRouter()
logger = logging.getLogger(__name__)

FAILED_TURN_COUNTER: dict[str, int] = {}
AI_FAILURE_COUNTER: dict[str, int] = {}
PENDING_CLARIFICATION: dict[str, str] = {}
APPOINTMENT_CONFIRMED: dict[str, bool] = {}
CALL_STT_MODE: dict[str, str] = {}
STT_EMPTY_TURNS: dict[str, int] = {}
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
    r"\b(human|real person|person|agent|representative|staff|operator)\b",
    re.IGNORECASE,
)
NAME_PATTERN = re.compile(
    r"\bmy name is\s+([a-zA-Z][a-zA-Z\s'\-]{1,40}?)(?=\s+(?:and|i)\b|[.,]|$)",
    re.IGNORECASE,
)
NAME_PATTERN_ALT = re.compile(
    r"\bmy name\s*(?:is)?\s+([a-zA-Z][a-zA-Z\s'\-]{1,40}?)(?=\s+(?:and|i)\b|[.,]|$)",
    re.IGNORECASE,
)
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
YES_PATTERN = re.compile(r"\b(yes|yeah|yep|correct|right)\b", re.IGNORECASE)
NO_PATTERN = re.compile(r"\b(no|nope|not that)\b", re.IGNORECASE)


def _should_end_call(text: str) -> bool:
    lowered = text.strip().lower()
    words = lowered.split()
    explicit = bool(
        re.search(
            r"\b(bye|goodbye|that's all|that is all|that's it|that will be all|no that's all)\b",
            lowered,
        )
    )
    short_thanks = bool(re.search(r"\b(thank you|thanks|thank u)\b", lowered)) and len(words) <= 6
    return explicit or short_thanks


def _normalized_stt_mode(mode: str | None) -> str:
    candidate = (mode or settings.stt_provider).strip().lower()
    if candidate in {"deepgram", "twilio_deepgram"}:
        return "deepgram"
    return "twilio"


def _collect_callback_url(stt_mode: str) -> str:
    return f"{settings.public_base_url}/api/calls/collect?stt_mode={stt_mode}"


def _extract_appointment_details(text: str) -> dict[str, str]:
    details: dict[str, str] = {}

    name_match = NAME_PATTERN.search(text)
    if not name_match:
        name_match = NAME_PATTERN_ALT.search(text)
    if name_match:
        details["patient_name"] = name_match.group(1).strip().rstrip(".")
    else:
        # Standalone likely-name utterance (e.g. "Rahul Singh")
        stripped = text.strip().strip(".")
        if re.fullmatch(r"[A-Za-z]{2,}(?:\s+[A-Za-z]{2,}){0,2}", stripped):
            details["patient_name"] = stripped

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


def _normalize_transcript_text(text: str) -> str:
    normalized = text
    normalized = AMBIGUOUS_CLINIC_PATTERN.sub("clinic hours", normalized)
    normalized = re.sub(r"\bappoint\s+an?\s+appointment\b", "appointment", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _split_clauses(text: str) -> list[str]:
    parts = re.split(r"[.?!]|,|\balso\b|\band\b", text, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p and p.strip()]


def _intent_specific_text(text: str, request_type: str) -> str:
    clauses = _split_clauses(text)
    if not clauses:
        return text
    matched: list[str] = []
    for clause in clauses:
        if infer_request_type(clause) == request_type:
            matched.append(clause)
    if matched:
        return ". ".join(matched)
    return text


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
    if request_type in {"medication_refill", "prescription_refill", "medical_question"}:
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
        notify_task_created(task)
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
    notify_task_created(task, escalation_reason=escalation_reason)


def _transfer_target_number(escalation_reason: str) -> str | None:
    if escalation_reason == "medical_emergency_keyword":
        return settings.clinic_doctor_number or settings.clinic_staff_number
    return settings.clinic_staff_number


def _render_bot_reply_twiml(bot_message: str, callback_url: str, stt_mode: str) -> str:
    try:
        audio_bytes = synthesize_tts(bot_message)
    except Exception as exc:
        logger.error("TTS synthesis failed provider=%s error=%s", settings.tts_provider, str(exc))
        audio_bytes = None
    if audio_bytes:
        audio_id = cache_audio(audio_bytes)
        audio_url = f"{settings.public_base_url}/api/calls/tts/{audio_id}"
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
        f"Our clinic staff will call you at {callback_number or 'your number on file'} to confirm. "
        "Can I help you with anything else?"
    )


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
    stt_mode = _normalized_stt_mode(CALL_STT_MODE.get(CallSid))
    callback_url = _collect_callback_url(stt_mode)
    if not call:
        call = Call(call_sid=CallSid, from_number=From, to_number=To, status=CallStatus)
        db.add(call)
        db.commit()
        stt_mode = _normalized_stt_mode(settings.stt_provider)
        CALL_STT_MODE[CallSid] = stt_mode
        STT_EMPTY_TURNS[CallSid] = 0
        callback_url = _collect_callback_url(stt_mode)
        APPOINTMENT_CONFIRMED[CallSid] = False
        bot_message = "Thank you for calling. I am the clinic assistant. How may I help you today?"
    else:
        # Twilio can retry /webhook callbacks; avoid restarting with full greeting.
        call.status = CallStatus or call.status
        db.commit()
        bot_message = "We are continuing your call. Please tell me how I can help."

    _append_transcript_line(db, CallSid, "BOT", bot_message)
    db.commit()
    logger.info("bot_reply call_sid=%s source=webhook text=%r", CallSid, bot_message)
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
    effective_stt_mode = _normalized_stt_mode(stt_mode or CALL_STT_MODE.get(CallSid))
    CALL_STT_MODE[CallSid] = effective_stt_mode
    raw_transcript_text = (SpeechResult or "").strip()
    callback_url = _collect_callback_url(effective_stt_mode)
    transcript_text = raw_transcript_text
    request_types: list[str] = []
    logger.info(
        "stt_turn call_sid=%s provider=%s confidence=%s transcript=%r",
        CallSid,
        effective_stt_mode,
        Confidence,
        raw_transcript_text,
    )

    # If Deepgram mode repeatedly returns empty/very low-confidence turns, fallback to Twilio STT.
    low_confidence = Confidence is not None and Confidence < settings.stt_low_confidence_threshold
    if effective_stt_mode == "deepgram" and (not raw_transcript_text or low_confidence):
        STT_EMPTY_TURNS[CallSid] = STT_EMPTY_TURNS.get(CallSid, 0) + 1
    else:
        STT_EMPTY_TURNS[CallSid] = 0
    if effective_stt_mode == "deepgram" and STT_EMPTY_TURNS.get(CallSid, 0) >= settings.stt_fallback_empty_turns:
        effective_stt_mode = "twilio"
        CALL_STT_MODE[CallSid] = effective_stt_mode
        STT_EMPTY_TURNS[CallSid] = 0
        callback_url = _collect_callback_url(effective_stt_mode)
        logger.warning("stt_fallback call_sid=%s switched_to=twilio", CallSid)

    if raw_transcript_text:
        _append_transcript_line(db, CallSid, "USER", raw_transcript_text)

        pending_normalized = PENDING_CLARIFICATION.get(CallSid)
        if pending_normalized:
            if YES_PATTERN.search(raw_transcript_text):
                transcript_text = pending_normalized
                PENDING_CLARIFICATION.pop(CallSid, None)
            elif NO_PATTERN.search(raw_transcript_text):
                # If caller says "no" but continues with a real request in the same utterance,
                # process it directly instead of trapping them in the clarification loop.
                remaining = NO_PATTERN.sub("", raw_transcript_text, count=1).strip(" ,.-")
                if remaining and infer_request_type(remaining) != "other":
                    transcript_text = remaining
                    PENDING_CLARIFICATION.pop(CallSid, None)
                else:
                    PENDING_CLARIFICATION.pop(CallSid, None)
                    bot_message = "Okay, please repeat your question in a different way."
                    _append_transcript_line(db, CallSid, "BOT", bot_message)
                    db.commit()
                    logger.info("bot_reply call_sid=%s source=clarification_no text=%r", CallSid, bot_message)
                    twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
                    return Response(content=twiml, media_type="application/xml")
            else:
                # Caller may answer clarification with a full request sentence.
                if infer_request_type(raw_transcript_text) != "other":
                    transcript_text = raw_transcript_text
                    PENDING_CLARIFICATION.pop(CallSid, None)
                else:
                    bot_message = "Please say yes if you meant clinic hours, or no to repeat your question."
                    _append_transcript_line(db, CallSid, "BOT", bot_message)
                    db.commit()
                    logger.info("bot_reply call_sid=%s source=clarification_pending text=%r", CallSid, bot_message)
                    twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
                    return Response(content=twiml, media_type="application/xml")
        elif AMBIGUOUS_CLINIC_PATTERN.search(raw_transcript_text):
            normalized_guess = _normalize_transcript_text(raw_transcript_text)
            PENDING_CLARIFICATION[CallSid] = normalized_guess
            bot_message = "I heard clinic hours. Did you mean clinic hours? Please say yes or no."
            _append_transcript_line(db, CallSid, "BOT", bot_message)
            db.commit()
            logger.info("bot_reply call_sid=%s source=clarification_prompt text=%r", CallSid, bot_message)
            twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
            return Response(content=twiml, media_type="application/xml")

        transcript_text = _normalize_transcript_text(transcript_text)
        immediate_escalation = _immediate_escalation_reason(transcript_text)

        request_types = infer_request_types(transcript_text)
        if not immediate_escalation:
            pending_appt_task = _upsert_pending_appointment_details(db, CallSid, transcript_text)
            for request_type in request_types:
                intent_text = _intent_specific_text(transcript_text, request_type)
                extracted = _extract_appointment_details(intent_text) if request_type == "appointment_scheduling" else {}
                if request_type == "appointment_scheduling":
                    # Keep intent-split protection, but inherit key appointment fields
                    # from the full utterance when the split clause dropped them.
                    full_extracted = _extract_appointment_details(transcript_text)
                    if not extracted.get("patient_name") and full_extracted.get("patient_name"):
                        extracted["patient_name"] = full_extracted["patient_name"]
                    if not extracted.get("appointment_type") and full_extracted.get("appointment_type"):
                        extracted["appointment_type"] = full_extracted["appointment_type"]
                _create_or_update_intent_task(
                    db=db,
                    call_sid=CallSid,
                    from_number=From,
                    request_type=request_type,
                    transcript_text=intent_text,
                    extracted=extracted,
                )

        db.commit()
    else:
        bot_message = "I did not catch that clearly. Please say your request again."
        _append_transcript_line(db, CallSid, "BOT", bot_message)
        db.commit()
        logger.info("bot_reply call_sid=%s source=silence_reprompt text=%r", CallSid, bot_message)
        twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
        return Response(content=twiml, media_type="application/xml")

    if _should_end_call(transcript_text):
        pending_count = (
            db.query(Task)
            .filter(Task.call_sid == CallSid, Task.status == "pending")
            .count()
        )
        FAILED_TURN_COUNTER.pop(CallSid, None)
        AI_FAILURE_COUNTER.pop(CallSid, None)
        PENDING_CLARIFICATION.pop(CallSid, None)
        APPOINTMENT_CONFIRMED.pop(CallSid, None)
        CALL_STT_MODE.pop(CallSid, None)
        STT_EMPTY_TURNS.pop(CallSid, None)
        if pending_count > 0:
            bot_message = "Thank you. I have created your request. Our clinic staff will call you back shortly. Goodbye."
        else:
            bot_message = "Thank you for calling the clinic. Goodbye."
        _append_transcript_line(db, CallSid, "BOT", bot_message)
        db.commit()
        logger.info("bot_reply call_sid=%s source=end_call text=%r", CallSid, bot_message)
        twiml = _render_bot_hangup_twiml(bot_message)
        return Response(content=twiml, media_type="application/xml")

    if immediate_escalation:
        db.add(
            Escalation(
                call_sid=CallSid,
                reason=immediate_escalation,
                details=transcript_text,
            )
        )
        _create_escalation_task(db, CallSid, From, immediate_escalation, transcript_text)
        bot_message = "I am escalating this call to our clinic staff right away for better support."
        _append_transcript_line(db, CallSid, "BOT", bot_message)
        db.commit()
        FAILED_TURN_COUNTER.pop(CallSid, None)
        AI_FAILURE_COUNTER.pop(CallSid, None)
        PENDING_CLARIFICATION.pop(CallSid, None)
        APPOINTMENT_CONFIRMED.pop(CallSid, None)
        CALL_STT_MODE.pop(CallSid, None)
        STT_EMPTY_TURNS.pop(CallSid, None)
        logger.info(
            "call_turn call_sid=%s transcript=%r openai_status=%s latency_ms=%s fallback=%s escalated=%s reason=%s",
            CallSid,
            transcript_text,
            "not_called_immediate_escalation",
            0,
            False,
            True,
            immediate_escalation,
        )
        logger.info("bot_reply call_sid=%s source=immediate_escalation text=%r", CallSid, bot_message)
        transfer_target = _transfer_target_number(immediate_escalation)
        if transfer_target:
            twiml = hold_and_dial(
                message=bot_message,
                target_number=transfer_target,
                hold_music_url=settings.hold_music_url,
            )
        else:
            twiml = hold_then_hangup(bot_message, hold_seconds=10)
        return Response(content=twiml, media_type="application/xml")

    # Deterministic appointment flow to avoid repetitive LLM loops.
    appt_task = (
        db.query(Task)
        .filter(
            Task.call_sid == CallSid,
            Task.status == "pending",
            Task.request_type == "appointment_scheduling",
        )
        .first()
    )
    if appt_task:
        if APPOINTMENT_CONFIRMED.get(CallSid):
            bot_message = (
                "I already have your appointment request. "
                "If you want to change details, say the new date, time, or appointment type. "
                "Otherwise, you can say thank you to end."
            )
        else:
            bot_message = _appointment_reply(appt_task, From)
            extracted = _get_extracted_from_task(appt_task)
            if extracted.get("preferred_schedule") and extracted.get("appointment_type") and (
                (appt_task.patient_name or "").strip() or extracted.get("patient_name")
            ):
                APPOINTMENT_CONFIRMED[CallSid] = True
        _append_transcript_line(db, CallSid, "BOT", bot_message)
        db.commit()
        logger.info("bot_reply call_sid=%s source=appointment_flow text=%r", CallSid, bot_message)
        twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
        return Response(content=twiml, media_type="application/xml")

    llm_start = perf_counter()
    llm_reply = generate_call_reply(transcript_text)
    latency_ms = int((perf_counter() - llm_start) * 1000)

    if llm_reply.fallback_used and llm_reply.openai_status.endswith("_empty_output"):
        FAILED_TURN_COUNTER[CallSid] = FAILED_TURN_COUNTER.get(CallSid, 0) + 1
    else:
        FAILED_TURN_COUNTER[CallSid] = 0

    if (
        llm_reply.openai_status.endswith("_request_exception")
        or llm_reply.openai_status.endswith("_empty_output")
        or "_http_" in llm_reply.openai_status
    ):
        AI_FAILURE_COUNTER[CallSid] = AI_FAILURE_COUNTER.get(CallSid, 0) + 1
    else:
        AI_FAILURE_COUNTER[CallSid] = 0

    escalation_reason = _escalation_reason(transcript_text, FAILED_TURN_COUNTER.get(CallSid, 0))
    if escalation_reason:
        db.add(
            Escalation(
                call_sid=CallSid,
                reason=escalation_reason,
                details=transcript_text,
            )
        )
        if escalation_reason in {"medical_emergency_keyword", "requested_human"}:
            _create_escalation_task(db, CallSid, From, escalation_reason, transcript_text)
        db.commit()
        FAILED_TURN_COUNTER.pop(CallSid, None)
        AI_FAILURE_COUNTER.pop(CallSid, None)
        PENDING_CLARIFICATION.pop(CallSid, None)
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
        bot_message = (
            "I am having trouble understanding consistently. "
            "You can continue, or say human anytime and I will connect staff."
        )
        _append_transcript_line(db, CallSid, "BOT", bot_message)
        db.commit()
        logger.info("bot_reply call_sid=%s source=understanding_guard text=%r", CallSid, bot_message)
        twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
        return Response(content=twiml, media_type="application/xml")

    if AI_FAILURE_COUNTER.get(CallSid, 0) >= 3:
        db.add(
            Escalation(
                call_sid=CallSid,
                reason="ai_service_instability",
                details=transcript_text,
            )
        )
        db.commit()
        AI_FAILURE_COUNTER.pop(CallSid, None)
        logger.info(
            "call_turn call_sid=%s transcript=%r openai_status=%s latency_ms=%s fallback=%s escalated=%s reason=%s",
            CallSid,
            transcript_text,
            llm_reply.openai_status,
            latency_ms,
            llm_reply.fallback_used,
            True,
            "ai_service_instability",
        )
        bot_message = (
            "I am having temporary technical issues. "
            "You can continue now, or say human to be connected with staff."
        )
        _append_transcript_line(db, CallSid, "BOT", bot_message)
        db.commit()
        logger.info("bot_reply call_sid=%s source=service_instability text=%r", CallSid, bot_message)
        twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
        return Response(content=twiml, media_type="application/xml")

    logger.info(
        "call_turn call_sid=%s transcript=%r openai_status=%s latency_ms=%s fallback=%s escalated=%s",
        CallSid,
        transcript_text,
        llm_reply.openai_status,
        latency_ms,
        llm_reply.fallback_used,
        False,
    )
    _append_transcript_line(db, CallSid, "BOT", llm_reply.text)
    db.commit()
    logger.info("bot_reply call_sid=%s source=llm text=%r", CallSid, llm_reply.text)
    twiml = _render_bot_reply_twiml(llm_reply.text, callback_url, stt_mode=effective_stt_mode)
    return Response(content=twiml, media_type="application/xml")


@router.get("/tts/{audio_id}")
def tts_audio(audio_id: str) -> Response:
    audio = get_cached_audio(audio_id)
    if not audio:
        raise HTTPException(status_code=404, detail="Audio not found")
    return Response(content=audio, media_type="audio/mpeg")
