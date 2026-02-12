from __future__ import annotations

import logging

import httpx

from app.core.config import settings
from app.models.task import Task


logger = logging.getLogger(__name__)


def _parse_numbers(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [n.strip() for n in raw.split(",") if n.strip()]


def _send_sms(to_number: str, body: str) -> None:
    if not (settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_phone_number):
        return
    url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
    data = {
        "To": to_number,
        "From": settings.twilio_phone_number,
        "Body": body[:1500],
    }
    with httpx.Client(timeout=8.0) as client:
        resp = client.post(url, data=data, auth=(settings.twilio_account_sid, settings.twilio_auth_token))
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500]
            logger.error("Twilio SMS API error to=%s status=%s body=%s", to_number, exc.response.status_code, detail)
            raise
        logger.warning("SMS sent successfully to=%s sid_hint=%s", to_number, resp.text[:120])


def notify_task_created(task: Task, escalation_reason: str | None = None) -> None:
    staff_numbers = _parse_numbers(settings.staff_notify_numbers)
    doctor_numbers = _parse_numbers(settings.doctor_notify_numbers)

    recipients: list[str]
    if escalation_reason == "medical_emergency_keyword":
        recipients = list(dict.fromkeys(staff_numbers + doctor_numbers))
    elif task.assigned_role == "doctor":
        recipients = doctor_numbers
    else:
        recipients = staff_numbers
    if not recipients:
        logger.warning(
            "No notification recipients for task_id=%s request_type=%s assigned_role=%s",
            task.id,
            task.request_type,
            task.assigned_role,
        )
        return

    body = (
        f"CliniqAI task alert: {task.request_type} | priority={task.priority} | "
        f"call_sid={task.call_sid} | callback={task.callback_number or 'N/A'}"
    )
    if escalation_reason:
        body += f" | escalation={escalation_reason}"

    for to_number in recipients:
        try:
            _send_sms(to_number, body)
        except Exception as exc:
            logger.error("SMS notify failed task_id=%s to=%s error=%s", task.id, to_number, str(exc))
