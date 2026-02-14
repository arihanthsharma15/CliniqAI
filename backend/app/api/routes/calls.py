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
from app.services.llm import generate_call_reply
from app.services.notifications import flush_call_notifications, queue_task_notification
from app.services.deepgram_stream import DeepgramRealtimeBridge
from app.services.tts import cache_audio, get_cached_audio, synthesize_tts

router = APIRouter()
logger = logging.getLogger(__name__)

FAILED_TURN_COUNTER: dict[str, int] = {}
AI_FAILURE_COUNTER: dict[str, int] = {}
PENDING_CLARIFICATION: dict[str, str] = {}
APPOINTMENT_CONFIRMED: dict[str, bool] = {}
NAME_CONFIRM_PENDING: dict[str, str] = {}
NAME_CONFIRMED: dict[str, bool] = {}
NAME_CONFIRM_RETRY: dict[str, int] = {}
NAME_SPELL_PENDING: dict[str, bool] = {}
CALL_STT_MODE: dict[str, str] = {}
STT_EMPTY_TURNS: dict[str, int] = {}
DEEPGRAM_FINAL_TRANSCRIPT: dict[str, str] = {}
DEEPGRAM_LATEST_TRANSCRIPT: dict[str, str] = {}
DEEPGRAM_BEST_TRANSCRIPT: dict[str, str] = {}
LAST_USER_TEXT: dict[str, str] = {}
LAST_BOT_TEXT: dict[str, str] = {}
BOT_REPEAT_COUNT: dict[str, int] = {}
NO_SPEECH_COUNT: dict[str, int] = {}
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
    r"\b(did you|have you|can you confirm|is my|what is|what's|confirm)\b",
    re.IGNORECASE,
)
CALLBACK_WINDOW_PATTERN = re.compile(
    r"\b(morning|afternoon|evening|\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)\b",
    re.IGNORECASE,
)
APPOINTMENT_CHANGE_PATTERN = re.compile(r"\b(change|reschedule|update|modify)\b", re.IGNORECASE)


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

    name_match = NAME_PATTERN.search(text)
    if not name_match:
        name_match = NAME_PATTERN_ALT.search(text)
    if name_match:
        details["patient_name"] = name_match.group(1).strip().rstrip(".")
    else:
        # Standalone likely-name utterance (e.g. "Rahul Singh")
        stripped = text.strip().strip(".")
        # Require at least two tokens to avoid treating random short words as names.
        if re.fullmatch(r"[A-Za-z]{2,}(?:\s+[A-Za-z]{2,}){1,2}", stripped):
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


def _set_pending_appointment_name(db: Session, call_sid: str, name: str | None) -> None:
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
        return
    details = _load_task_details(appt_task.details)
    extracted = details.get("extracted", {})
    if not isinstance(extracted, dict):
        extracted = {}
    if name:
        appt_task.patient_name = name
        extracted["patient_name"] = name
    else:
        appt_task.patient_name = None
        extracted.pop("patient_name", None)
    details["extracted"] = extracted
    appt_task.details = json.dumps(details)


def _display_name(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split())


def _spell_name(name: str) -> str:
    tokens = [tok for tok in name.split() if tok]
    spelled_tokens: list[str] = []
    for tok in tokens:
        letters = [ch.upper() for ch in tok if ch.isalpha()]
        if letters:
            spelled_tokens.append(" ".join(letters))
    return ", ".join(spelled_tokens)


def _extract_spelled_name(text: str) -> str | None:
    cleaned = re.sub(r"[^a-zA-Z\s]", " ", text).lower()
    tokens = [t for t in cleaned.split() if t]
    if not tokens:
        return None
    parts: list[str] = []
    letter_buf: list[str] = []
    for tok in tokens:
        if tok in {"and"}:
            if letter_buf:
                parts.append("".join(letter_buf))
                letter_buf = []
            continue
        if len(tok) == 1 and tok.isalpha():
            letter_buf.append(tok)
            continue
        if letter_buf:
            parts.append("".join(letter_buf))
            letter_buf = []
        if len(tok) >= 2:
            parts.append(tok)
    if letter_buf:
        parts.append("".join(letter_buf))
    if not parts:
        return None
    name = " ".join(p.capitalize() for p in parts[:3] if p)
    if len(name.replace(" ", "")) < 4:
        return None
    return name


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
    prev = LAST_USER_TEXT.get(call_sid, "").strip().lower()
    curr = current_text.strip().lower()
    if prev and curr and prev == curr:
        logger.warning("conversation_flag type=possible_misheard_or_repeat who=user")
    LAST_USER_TEXT[call_sid] = current_text


def _log_bot_turn_quality(call_sid: str, bot_text: str, user_text: str | None = None) -> None:
    prev_bot = LAST_BOT_TEXT.get(call_sid, "").strip().lower()
    curr_bot = bot_text.strip().lower()
    if prev_bot and curr_bot and prev_bot == curr_bot:
        BOT_REPEAT_COUNT[call_sid] = BOT_REPEAT_COUNT.get(call_sid, 0) + 1
    else:
        BOT_REPEAT_COUNT[call_sid] = 0
    if BOT_REPEAT_COUNT.get(call_sid, 0) >= 1:
        logger.warning("conversation_flag type=possible_bot_loop who=bot")
    elif user_text and user_text.strip():
        logger.info("conversation_health smooth=true")
    LAST_BOT_TEXT[call_sid] = bot_text


def _normalize_transcript_text(text: str) -> str:
    normalized = text
    normalized = AMBIGUOUS_CLINIC_PATTERN.sub("clinic hours", normalized)
    normalized = re.sub(r"\bappoint\s+an?\s+appointment\b", "appointment", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _split_clauses(text: str) -> list[str]:
    parts = re.split(r"[.?!]|,|\balso\b|\band\b", text, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p and p.strip()]


def _intent_specific_text(text: str, request_type: str, fallback_to_full: bool = True) -> str:
    clauses = _split_clauses(text)
    if not clauses:
        return text
    matched: list[str] = []
    for clause in clauses:
        if infer_request_type(clause) == request_type:
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
    queue_task_notification(task, escalation_reason=escalation_reason)


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


def _status_reply(db: Session, call_sid: str, text: str) -> str | None:
    lowered = text.lower()
    asks_appointment_status = bool(APPOINTMENT_STATUS_PATTERN.search(lowered)) and bool(STATUS_QUESTION_PATTERN.search(lowered))
    callback_mentioned = bool(CALLBACK_STATUS_PATTERN.search(lowered))
    asks_callback_status = callback_mentioned and (
        bool(STATUS_QUESTION_PATTERN.search(lowered))
        or lowered.strip() in {"callback", "the callback", "and the callback"}
    )
    if not asks_appointment_status and not asks_callback_status:
        return None
    if not asks_appointment_status and not asks_callback_status:
        return None

    appt_task = (
        db.query(Task)
        .filter(
            Task.call_sid == call_sid,
            Task.status == "pending",
            Task.request_type == "appointment_scheduling",
        )
        .first()
    )
    callback_task = (
        db.query(Task)
        .filter(
            Task.call_sid == call_sid,
            Task.status == "pending",
            Task.request_type == "callback_request",
        )
        .first()
    )

    if asks_appointment_status and appt_task:
        extracted = _get_extracted_from_task(appt_task)
        schedule = extracted.get("preferred_schedule", "your requested time")
        return f"Yes, your appointment request is captured for {schedule}. The clinic staff will confirm it with you."
    if asks_callback_status and callback_task:
        window = _callback_window_from_task(callback_task) or "your requested time window"
        return f"Yes, I created your callback request. Our staff will call you in {window}."
    if asks_appointment_status and not appt_task:
        return "I do not have a complete appointment request yet. Please tell me your preferred date and time."
    if asks_callback_status and not callback_task:
        return "I do not have a callback request yet. Please tell me when you want a callback."
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
        if (CallStatus or "").lower() in {"completed", "canceled", "busy", "failed", "no-answer"}:
            flush_call_notifications(CallSid)
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
    effective_stt_mode = _normalized_stt_mode(stt_mode or CALL_STT_MODE.get(CallSid))
    CALL_STT_MODE[CallSid] = effective_stt_mode
    raw_transcript_text = (SpeechResult or "").strip()
    if _use_direct_deepgram_streaming() and settings.ws_brain_mode:
        deepgram_final = DEEPGRAM_FINAL_TRANSCRIPT.pop(CallSid, "").strip()
        deepgram_latest = DEEPGRAM_LATEST_TRANSCRIPT.pop(CallSid, "").strip()
        deepgram_best = DEEPGRAM_BEST_TRANSCRIPT.pop(CallSid, "").strip()
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
        logger.warning("stt_fallback switched_to=twilio")

    if raw_transcript_text:
        NO_SPEECH_COUNT[CallSid] = 0
        _append_transcript_line(db, CallSid, "USER", raw_transcript_text)
        _log_user_turn_quality(CallSid, raw_transcript_text)

        if NAME_SPELL_PENDING.get(CallSid):
            spelled_name = _extract_spelled_name(raw_transcript_text)
            if spelled_name:
                _set_pending_appointment_name(db, CallSid, spelled_name)
                NAME_SPELL_PENDING.pop(CallSid, None)
                NAME_CONFIRM_PENDING[CallSid] = spelled_name
                NAME_CONFIRMED[CallSid] = False
                NAME_CONFIRM_RETRY[CallSid] = 0
                db.commit()
                spelling = _spell_name(spelled_name)
                bot_message = (
                    f"I heard your name as {_display_name(spelled_name)}"
                    + (f", spelled {spelling}" if spelling else "")
                    + ". Is that correct? Please say yes or no."
                )
                _append_transcript_line(db, CallSid, "BOT", bot_message)
                db.commit()
                _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
                twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
                return Response(content=twiml, media_type="application/xml")
            bot_message = (
                "Please spell your first and last name letter by letter, for example "
                "R A H U L S I N G H."
            )
            _append_transcript_line(db, CallSid, "BOT", bot_message)
            db.commit()
            _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
            twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
            return Response(content=twiml, media_type="application/xml")

        pending_name = NAME_CONFIRM_PENDING.get(CallSid)
        if pending_name:
            if YES_PATTERN.search(raw_transcript_text):
                NAME_CONFIRMED[CallSid] = True
                NAME_CONFIRM_PENDING.pop(CallSid, None)
                NAME_CONFIRM_RETRY[CallSid] = 0
                bot_message = "Great, thank you. Please continue."
                _append_transcript_line(db, CallSid, "BOT", bot_message)
                db.commit()
                _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
                twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
                return Response(content=twiml, media_type="application/xml")
            if NO_PATTERN.search(raw_transcript_text):
                NAME_CONFIRMED[CallSid] = False
                NAME_CONFIRM_PENDING.pop(CallSid, None)
                NAME_CONFIRM_RETRY[CallSid] = NAME_CONFIRM_RETRY.get(CallSid, 0) + 1
                _set_pending_appointment_name(db, CallSid, None)
                db.commit()
                if NAME_CONFIRM_RETRY[CallSid] >= 2:
                    bot_message = (
                        "Let's do this quickly. Please spell your first and last name letter by letter."
                    )
                    NAME_SPELL_PENDING[CallSid] = True
                else:
                    bot_message = "Sorry about that. Please say your full name again."
                _append_transcript_line(db, CallSid, "BOT", bot_message)
                db.commit()
                _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
                twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
                return Response(content=twiml, media_type="application/xml")
            corrected = _extract_appointment_details(raw_transcript_text).get("patient_name")
            if corrected:
                corrected = corrected.strip()
                _set_pending_appointment_name(db, CallSid, corrected)
                NAME_CONFIRM_PENDING[CallSid] = corrected
                NAME_CONFIRMED[CallSid] = False
                NAME_CONFIRM_RETRY[CallSid] = 0
                db.commit()
                spelling = _spell_name(corrected)
                bot_message = (
                    f"I heard your name as {_display_name(corrected)}"
                    + (f", spelled {spelling}" if spelling else "")
                    + ". Is that correct? Please say yes or no."
                )
                _append_transcript_line(db, CallSid, "BOT", bot_message)
                db.commit()
                _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
                twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
                return Response(content=twiml, media_type="application/xml")
            bot_message = "Please say yes if the name is correct, or say your full name again."
            _append_transcript_line(db, CallSid, "BOT", bot_message)
            db.commit()
            _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
            twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
            return Response(content=twiml, media_type="application/xml")

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
                    _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
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
                    _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
                    logger.info("bot_reply call_sid=%s source=clarification_pending text=%r", CallSid, bot_message)
                    twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
                    return Response(content=twiml, media_type="application/xml")
        elif AMBIGUOUS_CLINIC_PATTERN.search(raw_transcript_text):
            normalized_guess = _normalize_transcript_text(raw_transcript_text)
            PENDING_CLARIFICATION[CallSid] = normalized_guess
            bot_message = "I heard clinic hours. Did you mean clinic hours? Please say yes or no."
            _append_transcript_line(db, CallSid, "BOT", bot_message)
            db.commit()
            _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
            logger.info("bot_reply call_sid=%s source=clarification_prompt text=%r", CallSid, bot_message)
            twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
            return Response(content=twiml, media_type="application/xml")

        transcript_text = _normalize_transcript_text(transcript_text)
        immediate_escalation = _immediate_escalation_reason(transcript_text)

        status_message = _status_reply(db, CallSid, transcript_text)
        if status_message:
            _append_transcript_line(db, CallSid, "BOT", status_message)
            db.commit()
            _log_bot_turn_quality(CallSid, status_message, raw_transcript_text)
            twiml = _render_bot_reply_twiml(status_message, callback_url, stt_mode=effective_stt_mode)
            return Response(content=twiml, media_type="application/xml")

        request_types = infer_request_types(transcript_text)
        if not immediate_escalation:
            use_strict_intent_split = len(request_types) > 1
            appt_intent_text = _intent_specific_text(
                transcript_text,
                "appointment_scheduling",
                fallback_to_full=not use_strict_intent_split,
            )
            pending_appt_task = _upsert_pending_appointment_details(db, CallSid, appt_intent_text)
            for request_type in request_types:
                intent_text = _intent_specific_text(
                    transcript_text,
                    request_type,
                    fallback_to_full=not use_strict_intent_split,
                )
                if not intent_text.strip():
                    continue
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
            if (
                pending_appt_task
                and pending_appt_task.patient_name
                and not NAME_CONFIRMED.get(CallSid)
                and NAME_CONFIRM_RETRY.get(CallSid, 0) < 2
            ):
                name_for_confirm = pending_appt_task.patient_name.strip()
                if name_for_confirm:
                    NAME_CONFIRM_PENDING[CallSid] = name_for_confirm
                    spelling = _spell_name(name_for_confirm)
                    bot_message = (
                        f"I heard your name as {_display_name(name_for_confirm)}. "
                        + (f"Spelled {spelling}. " if spelling else "")
                        + "Is that correct? Please say yes or no."
                    )
                    _append_transcript_line(db, CallSid, "BOT", bot_message)
                    db.commit()
                    _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
                    twiml = _render_bot_reply_twiml(bot_message, callback_url, stt_mode=effective_stt_mode)
                    return Response(content=twiml, media_type="application/xml")

        db.commit()
    else:
        NO_SPEECH_COUNT[CallSid] = NO_SPEECH_COUNT.get(CallSid, 0) + 1
        if _use_direct_deepgram_streaming() and settings.ws_brain_mode and NO_SPEECH_COUNT[CallSid] <= 1:
            # In stream mode, brief empty callbacks are common; skip noisy reprompt once.
            twiml = _render_bot_reply_twiml("Please continue.", callback_url, stt_mode=effective_stt_mode)
            return Response(content=twiml, media_type="application/xml")
        bot_message = "I did not catch that clearly. Please say your request again."
        _append_transcript_line(db, CallSid, "BOT", bot_message)
        db.commit()
        _log_bot_turn_quality(CallSid, bot_message)
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
        NAME_CONFIRM_PENDING.pop(CallSid, None)
        NAME_CONFIRMED.pop(CallSid, None)
        NAME_CONFIRM_RETRY.pop(CallSid, None)
        NAME_SPELL_PENDING.pop(CallSid, None)
        CALL_STT_MODE.pop(CallSid, None)
        STT_EMPTY_TURNS.pop(CallSid, None)
        DEEPGRAM_FINAL_TRANSCRIPT.pop(CallSid, None)
        DEEPGRAM_LATEST_TRANSCRIPT.pop(CallSid, None)
        DEEPGRAM_BEST_TRANSCRIPT.pop(CallSid, None)
        LAST_USER_TEXT.pop(CallSid, None)
        LAST_BOT_TEXT.pop(CallSid, None)
        BOT_REPEAT_COUNT.pop(CallSid, None)
        NO_SPEECH_COUNT.pop(CallSid, None)
        if pending_count > 0:
            bot_message = "Thank you. I have created your request. Our clinic staff will call you back shortly. Goodbye."
        else:
            bot_message = "Thank you for calling the clinic. Goodbye."
        _append_transcript_line(db, CallSid, "BOT", bot_message)
        db.commit()
        _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
        flush_call_notifications(CallSid)
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
        _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
        flush_call_notifications(CallSid)
        FAILED_TURN_COUNTER.pop(CallSid, None)
        AI_FAILURE_COUNTER.pop(CallSid, None)
        PENDING_CLARIFICATION.pop(CallSid, None)
        APPOINTMENT_CONFIRMED.pop(CallSid, None)
        NAME_CONFIRM_PENDING.pop(CallSid, None)
        NAME_CONFIRMED.pop(CallSid, None)
        NAME_CONFIRM_RETRY.pop(CallSid, None)
        NAME_SPELL_PENDING.pop(CallSid, None)
        CALL_STT_MODE.pop(CallSid, None)
        STT_EMPTY_TURNS.pop(CallSid, None)
        DEEPGRAM_FINAL_TRANSCRIPT.pop(CallSid, None)
        DEEPGRAM_LATEST_TRANSCRIPT.pop(CallSid, None)
        DEEPGRAM_BEST_TRANSCRIPT.pop(CallSid, None)
        LAST_USER_TEXT.pop(CallSid, None)
        LAST_BOT_TEXT.pop(CallSid, None)
        BOT_REPEAT_COUNT.pop(CallSid, None)
        NO_SPEECH_COUNT.pop(CallSid, None)
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
        lowered_turn = transcript_text.lower()
        if APPOINTMENT_CONFIRMED.get(CallSid):
            callback_task = (
                db.query(Task)
                .filter(
                    Task.call_sid == CallSid,
                    Task.status == "pending",
                    Task.request_type == "callback_request",
                )
                .first()
            )
            if CALLBACK_STATUS_PATTERN.search(lowered_turn) and callback_task:
                window = _callback_window_from_task(callback_task) or "your requested time window"
                bot_message = f"Yes, I noted your callback request. Staff will call you in {window}. Anything else?"
            elif APPOINTMENT_STATUS_PATTERN.search(lowered_turn):
                extracted = _get_extracted_from_task(appt_task)
                schedule = extracted.get("preferred_schedule", "your requested time")
                bot_message = f"Yes, your appointment request is set for {schedule}. Anything else?"
            elif APPOINTMENT_CHANGE_PATTERN.search(lowered_turn):
                bot_message = "Sure, I can update that. Please tell me the new preferred date and time."
            else:
                bot_message = (
                    "I have your appointment request. "
                    "You can ask me to confirm details, add a callback request, or say thank you to end."
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
        _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
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
    if escalation_reason == "failed_understanding_3_turns" and APPOINTMENT_CONFIRMED.get(CallSid):
        escalation_reason = None
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
        _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
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
        _log_bot_turn_quality(CallSid, bot_message, raw_transcript_text)
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
    _log_bot_turn_quality(CallSid, llm_reply.text, raw_transcript_text)
    logger.info("bot_reply call_sid=%s source=llm text=%r", CallSid, llm_reply.text)
    twiml = _render_bot_reply_twiml(llm_reply.text, callback_url, stt_mode=effective_stt_mode)
    return Response(content=twiml, media_type="application/xml")


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
        DEEPGRAM_LATEST_TRANSCRIPT[call_sid] = transcript
        prev_best = DEEPGRAM_BEST_TRANSCRIPT.get(call_sid, "")
        if len(transcript.strip()) >= len(prev_best.strip()):
            DEEPGRAM_BEST_TRANSCRIPT[call_sid] = transcript
        if is_final:
            DEEPGRAM_FINAL_TRANSCRIPT[call_sid] = transcript
            logger.warning("deepgram_final transcript=%r", transcript)
        else:
            logger.warning("deepgram_partial transcript=%r", transcript)

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
