import json
import logging
import re
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import settings
from app.core.twiml import gather_speech, say_and_gather, say_and_hangup
from app.models.call import Call
from app.models.escalation import Escalation
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.intent import infer_request_type
from app.services.llm import generate_call_reply

router = APIRouter()
logger = logging.getLogger(__name__)

FAILED_TURN_COUNTER: dict[str, int] = {}
AI_FAILURE_COUNTER: dict[str, int] = {}

EMERGENCY_PATTERN = re.compile(
    r"\b(chest pain|can't breathe|cannot breathe|bleeding|unconscious|suicide|ambulance|emergency)\b",
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


def _extract_appointment_details(text: str) -> dict[str, str]:
    details: dict[str, str] = {}

    name_match = NAME_PATTERN.search(text)
    if name_match:
        details["patient_name"] = name_match.group(1).strip().rstrip(".")

    date_time = DATE_TIME_PATTERN.findall(text)
    if date_time:
        details["preferred_schedule"] = ", ".join(dict.fromkeys(item.strip() for item in date_time))

    reason_match = VISIT_REASON_PATTERN.search(text)
    if reason_match:
        details["visit_reason"] = reason_match.group(1).strip()

    return details


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


def _escalation_reason(text: str, failed_turns: int) -> str | None:
    if EMERGENCY_PATTERN.search(text):
        return "medical_emergency_keyword"
    if HUMAN_REQUEST_PATTERN.search(text):
        return "requested_human"
    if failed_turns >= 3:
        return "failed_understanding_3_turns"
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
    if not call:
        call = Call(call_sid=CallSid, from_number=From, to_number=To, status=CallStatus)
        db.add(call)
        db.commit()

    twiml = gather_speech(
        "Thank you for calling. I am the clinic assistant.",
        f"{settings.public_base_url}/api/calls/collect",
    )
    return Response(content=twiml, media_type="application/xml")


@router.post("/collect")
def collect_speech(
    CallSid: str = Form(...),
    From: str | None = Form(None),
    SpeechResult: str | None = Form(None),
    Confidence: float | None = Form(None),
    db: Session = Depends(get_db),
) -> Response:
    transcript_text = SpeechResult or ""
    callback_url = f"{settings.public_base_url}/api/calls/collect"

    if transcript_text:
        # Keep one transcript row per call and append each new utterance.
        transcript = (
            db.query(Transcript)
            .filter(Transcript.call_sid == CallSid)
            .order_by(Transcript.id.desc())
            .first()
        )
        if transcript:
            transcript.text = f"{transcript.text}\n{transcript_text}".strip()
        else:
            transcript = Transcript(call_sid=CallSid, text=transcript_text)
            db.add(transcript)

        request_type = infer_request_type(transcript_text)
        extracted = _extract_appointment_details(transcript_text) if request_type == "appointment_scheduling" else {}

        # Check existing pending task for this call
        existing_task = (
            db.query(Task)
            .filter(
                Task.call_sid == CallSid,
                Task.status == "pending",
            )
            .first()
        )

        # If appointment info appears in follow-up turns, merge it into the pending appointment task
        # even when current turn intent is detected as "other".
        if existing_task and existing_task.request_type == "appointment_scheduling":
            merged_extracted = _extract_appointment_details(transcript_text)
            if merged_extracted:
                try:
                    existing_details = json.loads(existing_task.details) if existing_task.details else {}
                except json.JSONDecodeError:
                    existing_details = {"raw_request": existing_task.details or "", "extracted": {}}
                existing_extracted = existing_details.get("extracted", {})
                if not isinstance(existing_extracted, dict):
                    existing_extracted = {}
                existing_extracted = _merge_extracted(existing_extracted, merged_extracted)
                existing_details["extracted"] = existing_extracted
                existing_task.details = json.dumps(existing_details)
                if merged_extracted.get("patient_name"):
                    existing_task.patient_name = merged_extracted["patient_name"]

        if request_type:
            if not existing_task:
                task = Task(
                    call_sid=CallSid,
                    patient_name=extracted.get("patient_name"),
                    callback_number=From,
                    request_type=request_type,
                    priority="normal",
                    details=json.dumps(
                        {
                            "raw_request": transcript_text,
                            "extracted": extracted,
                        }
                    ),
                )
                db.add(task)
            elif request_type == "appointment_scheduling":
                try:
                    existing_details = json.loads(existing_task.details) if existing_task.details else {}
                except json.JSONDecodeError:
                    existing_details = {"raw_request": existing_task.details or "", "extracted": {}}
                existing_extracted = existing_details.get("extracted", {})
                if not isinstance(existing_extracted, dict):
                    existing_extracted = {}
                existing_extracted = _merge_extracted(existing_extracted, extracted)
                existing_details["extracted"] = existing_extracted
                existing_task.details = json.dumps(existing_details)
                if extracted.get("patient_name"):
                    existing_task.patient_name = extracted["patient_name"]

        db.commit()
    else:
        twiml = say_and_gather("I did not catch that clearly. Please say your request again.", callback_url)
        return Response(content=twiml, media_type="application/xml")

    if _should_end_call(transcript_text):
        FAILED_TURN_COUNTER.pop(CallSid, None)
        AI_FAILURE_COUNTER.pop(CallSid, None)
        twiml = say_and_hangup("Thank you for calling the clinic. Goodbye.")
        return Response(content=twiml, media_type="application/xml")

    llm_start = perf_counter()
    llm_reply = generate_call_reply(transcript_text)
    latency_ms = int((perf_counter() - llm_start) * 1000)

    if llm_reply.fallback_used and llm_reply.openai_status == "empty_output":
        FAILED_TURN_COUNTER[CallSid] = FAILED_TURN_COUNTER.get(CallSid, 0) + 1
    else:
        FAILED_TURN_COUNTER[CallSid] = 0

    if llm_reply.openai_status in {"request_exception", "empty_output"} or llm_reply.openai_status.startswith("http_"):
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
        db.commit()
        FAILED_TURN_COUNTER.pop(CallSid, None)
        AI_FAILURE_COUNTER.pop(CallSid, None)
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
        if escalation_reason in {"medical_emergency_keyword", "requested_human"}:
            twiml = say_and_hangup(
                "I am connecting this request to our staff for faster help. "
                "They will call you back shortly. Goodbye."
            )
            return Response(content=twiml, media_type="application/xml")
        twiml = say_and_gather(
            "I am having trouble understanding consistently. "
            "You can continue, or say human anytime and I will connect staff.",
            callback_url,
        )
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
        twiml = say_and_gather(
            "I am having temporary technical issues. "
            "You can continue now, or say human to be connected with staff.",
            callback_url,
        )
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
    twiml = say_and_gather(llm_reply.text, callback_url)
    return Response(content=twiml, media_type="application/xml")
