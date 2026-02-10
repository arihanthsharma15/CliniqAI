from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.twiml import gather_speech, say_response
from app.models.call import Call
from app.models.task import Task
from app.models.transcript import Transcript
from app.services.intent import infer_request_type

router = APIRouter()


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
        "/api/calls/collect",
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

    request_created = False

    if transcript_text:
        # Save transcript
        transcript = Transcript(call_sid=CallSid, text=transcript_text)
        db.add(transcript)

        request_type = infer_request_type(transcript_text)

        if request_type:
            # Check existing pending task for this call
            existing_task = (
                db.query(Task)
                .filter(
                    Task.call_sid == CallSid,
                    Task.status == "pending",
                )
                .first()
            )

            if not existing_task:
                task = Task(
                    call_sid=CallSid,
                    patient_name=None,
                    callback_number=From,
                    request_type=request_type,
                    priority="normal",
                    details=transcript_text,
                )
                db.add(task)
                request_created = True

        db.commit()

    if request_created:
        message = (
            "Thanks. I have created a request. "
            "Our staff will call you back to confirm details."
        )
    else:
        message = (
            "Sorry, I didn't fully understand. "
            "Could you please repeat your request?"
        )

    twiml = say_response(message)
    return Response(content=twiml, media_type="application/xml")