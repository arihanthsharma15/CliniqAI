from __future__ import annotations

import logging
from collections import defaultdict

import httpx

from app.core.config import settings
from app.models.task import Task


logger = logging.getLogger(__name__)
PENDING_CALL_ALERTS: dict[str, list[dict[str, str]]] = defaultdict(list)


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


def _build_recipients(call_items: list[dict[str, str]]) -> list[str]:
    staff_numbers = _parse_numbers(settings.staff_notify_numbers)
    doctor_numbers = _parse_numbers(settings.doctor_notify_numbers)

    need_staff = any(item.get("assigned_role") == "staff" for item in call_items)
    need_doctor = any(item.get("assigned_role") == "doctor" for item in call_items)
    has_medical_emergency = any(item.get("escalation_reason") == "medical_emergency_keyword" for item in call_items)
    if has_medical_emergency:
        need_staff = True
        need_doctor = True

    recipients: list[str] = []
    if need_staff:
        recipients.extend(staff_numbers)
    if need_doctor:
        recipients.extend(doctor_numbers)
    return list(dict.fromkeys(recipients))


def queue_task_notification(task: Task, escalation_reason: str | None = None) -> None:
    call_sid = (task.call_sid or "").strip()
    if not call_sid:
        return
    PENDING_CALL_ALERTS[call_sid].append(
        {
            "request_type": task.request_type,
            "priority": task.priority,
            "assigned_role": task.assigned_role,
            "callback_number": task.callback_number or "",
            "escalation_reason": escalation_reason or "",
        }
    )


def flush_call_notifications(call_sid: str) -> None:
    call_sid = (call_sid or "").strip()
    if not call_sid:
        return
    items = PENDING_CALL_ALERTS.pop(call_sid, [])
    if not items:
        return
    recipients = _build_recipients(items)
    if not recipients:
        logger.warning(
            "No notification recipients for call_sid=%s items=%s",
            call_sid,
            len(items),
        )
        return

    callback = next((i.get("callback_number") for i in items if i.get("callback_number")), "") or "N/A"
    summary = []
    for i, item in enumerate(items, start=1):
        line = f"{i}) {item.get('request_type','task')}"
        esc = item.get("escalation_reason")
        if esc:
            line += f" [{esc}]"
        summary.append(line)
    body = (
        f"CliniqAI call summary | call_sid={call_sid} | callback={callback}\n"
        + "\n".join(summary[:8])
    )

    for to_number in recipients:
        try:
            _send_sms(to_number, body)
        except Exception as exc:
            logger.error("SMS notify failed call_sid=%s to=%s error=%s", call_sid, to_number, str(exc))
