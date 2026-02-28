from __future__ import annotations
from typing import Tuple
from app.services.context import get_context, update_context
import re


def _empty(v) -> bool:
    return not v or str(v).strip().lower() in ("", "none")


def _missing_slot(slots: dict):
    if _empty(slots.get("name")): return "name"
    if _empty(slots.get("appointment_type")): return "appointment_type"
    if _empty(slots.get("date")): return "date"
    if _empty(slots.get("time")): return "time"
    return None


def _missing_callback_slot(slots: dict):
    if _empty(slots.get("name")): return "name"
    if _empty(slots.get("callback_time")): return "callback_time"
    return None

def _missing_refill_slot(slots: dict):
    if _empty(slots.get("name")): return "name"
    return None


def next_state(call_sid: str, intent: str, entities: dict, transcript_text: str = "") -> Tuple[str, str, dict]:
    ctx = get_context(call_sid)
    state = ctx.get("state", "GREETING")
    slots = ctx.get("slots") or {}

    # --------------------------------------------------
    # GLOBAL: HOURS CHECK
    # --------------------------------------------------
    if intent == "clinic_hours":
        if state.startswith("APPOINTMENT"):
            missing = _missing_slot(slots)
            return state, f"Tell the user staff will confirm hours during the callback. Then ask for their {missing}.", slots
        if state.startswith("CALLBACK"):
            missing = _missing_callback_slot(slots)
            return state, f"Tell the user staff will confirm hours during the callback. Then ask: what day works best for your appointment?", slots
        return "GENERAL", "Tell them staff will confirm hours later. Then ask: 'Is there anything else I can help you with?'" , slots

    # --------------------------------------------------
    # GLOBAL: START FLOWS (only if not already mid-flow)
    # --------------------------------------------------
    if intent == "appointment" and not state.startswith("APPOINTMENT") and state != "POST_TASK":
        slots["task_type"] = "appointment"
        return "APPOINTMENT_NAME", "STRICT: Acknowledge and ask: 'Sure, may I have your full name please?'", slots

    if intent == "refill" and not state.startswith("APPOINTMENT") and state != "POST_TASK":
        slots["task_type"] = "refill"
        return "APPOINTMENT_NAME", "STRICT: Acknowledge the refill request and ask: 'Sure, may I have your full name please?'", slots

    if intent == "callback" and not state.startswith("CALLBACK") and state != "POST_TASK":
        slots["task_type"] = "callback"
        return "CALLBACK_NAME", "STRICT: Acknowledge and ask: 'Sure, may I have your full name please?'", slots

    # --------------------------------------------------
    # GREETING -> GENERAL
    # --------------------------------------------------
    if state == "GREETING":
        return "GENERAL", "Greet the user briefly and ask how you can help.", slots

    # --------------------------------------------------
    # APPOINTMENT / REFILL SLOT FILLING
    # --------------------------------------------------
    if state.startswith("APPOINTMENT"):
        for k in ["name", "date", "time", "appointment_type"]:
            if entities.get(k):
                slots[k] = entities[k]

        if state == "APPOINTMENT_NAME" and _empty(slots.get("name")):
            candidate = transcript_text.strip()
            if candidate and len(candidate) > 2:
                slots["name"] = candidate.title()

        if state == "APPOINTMENT_TIME" and _empty(slots.get("time")):
            candidate = transcript_text.strip()
            time_words = {"morning", "afternoon", "evening", "night", "noon", "midnight"}
            time_pattern = re.compile(r"\d{1,2}(?::\d{2})?\s*(?:am|pm)|o'?clock", re.I)
            if candidate and (candidate.lower() in time_words or time_pattern.search(candidate)):
                slots["time"] = candidate

        task_type = slots.get("task_type")
        if task_type == "refill":
            missing = _missing_refill_slot(slots)
        else:
            missing = _missing_slot(slots)

        if not missing:
            return "POST_TASK", "STRICT: Tell them 'I've created that task. Staff will call you back shortly. Is there anything else I can help you with?", slots

        new_state = f"APPOINTMENT_{missing.upper()}"
        prompts = {
            "name": "Say exactly: 'Sure, may I have your full name please?'",
            "appointment_type": "Say exactly: 'What type of appointment do you need?'",
            "date": "Say exactly: 'What day works best for you?'",
            "time": "Say exactly: 'What time of day do you prefer?'",
        }
        return new_state, f"STRICT: {prompts[missing]}", slots

    # --------------------------------------------------
    # CALLBACK SLOT FILLING
    # --------------------------------------------------
    if state.startswith("CALLBACK"):
        for k in ["name", "callback_time"]:
            if entities.get(k):
                slots[k] = entities[k]

        if state == "CALLBACK_NAME" and _empty(slots.get("name")):
            candidate = transcript_text.strip()
            if candidate and len(candidate) > 2:
                slots["name"] = candidate.title()

        if state == "CALLBACK_TIME" and _empty(slots.get("callback_time")):
            candidate = transcript_text.strip()
            if candidate:
                slots["callback_time"] = candidate

        missing = _missing_callback_slot(slots)

        if not missing:
            return "POST_TASK", "STRICT: Tell them 'I've noted your callback request. Staff will call you back shortly. Is there anything else I can help you with?", slots

        new_state = f"CALLBACK_{missing.upper()}"
        prompts = {
         "name": "Say exactly: 'Sure, may I have your full name please?'",
         "callback_time": "Say exactly: 'What time of day works best for the callback?'",
        }

        return new_state, f"STRICT: {prompts[missing]}", slots

    # --------------------------------------------------
    # POST_TASK / EXIT
    # --------------------------------------------------
    if state == "POST_TASK":
        if intent == "exit":
            return "END_CALL", "Say goodbye and hang up.", slots
        return "POST_TASK", "Ask if there is anything else they need help with.", slots

    if state == "END_CALL" or intent == "exit":
        return "END_CALL", "Say goodbye.", slots

    # DEFAULT FALLBACK
    return "GENERAL", "Ask how you can help today.", slots