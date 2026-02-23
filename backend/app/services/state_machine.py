from __future__ import annotations

from typing import Tuple
from app.services.context import get_context, update_context


# -----------------------------
# Helpers
# -----------------------------

def _missing_slot(slots: dict):
    if not slots.get("name"):
        return "name"
    if not slots.get("task_type"):
        return "task_type"
    if not slots.get("date"):
        return "date"
    if not slots.get("time"):
        return "time"
    return None


# -----------------------------
# MAIN STATE ROUTER
# -----------------------------

def next_state(call_sid: str, intent: str, entities: dict) -> Tuple[str, str]:
    """
    Returns:
        (state, instruction_for_llm)
    """

    ctx = get_context(call_sid)
    state = ctx["state"]
    slots = ctx["slots"]

    # ---------------- GREETING ----------------
    if state == "GREETING":
        update_context(call_sid, {"state": "GENERAL"})
        return "GENERAL", "Greet user and ask how you can help."

    # ---------------- GENERAL QUERY ----------------
    if intent == "general":
        return "GENERAL", "Answer user's question briefly and ask if anything else is needed."

    # ---------------- TASK START ----------------
    if intent in ["appointment", "refill"]:
        slots["task_type"] = intent
        update_context(call_sid, {"state": "APPOINTMENT_NAME", "slots": slots})
        return "APPOINTMENT_NAME", "Ask user name for booking."

    # ---------------- SLOT FILLING ----------------
    if state.startswith("APPOINTMENT"):

        # fill entities automatically
        for k in ["name", "date", "time"]:
            if entities.get(k):
                slots[k] = entities[k]

        missing = _missing_slot(slots)

        if missing == "name":
            update_context(call_sid, {"state": "APPOINTMENT_NAME", "slots": slots})
            return "APPOINTMENT_NAME", "Ask user's name."

        if missing == "task_type":
            update_context(call_sid, {"state": "APPOINTMENT_TYPE", "slots": slots})
            return "APPOINTMENT_TYPE", "Ask appointment or refill."

        if missing == "date":
            update_context(call_sid, {"state": "APPOINTMENT_DATE", "slots": slots})
            return "APPOINTMENT_DATE", "Ask appointment date."

        if missing == "time":
            update_context(call_sid, {"state": "APPOINTMENT_TIME", "slots": slots})
            return "APPOINTMENT_TIME", "Ask appointment time."

        # ALL FILLED
        update_context(call_sid, {
            "state": "TASK_CONFIRMED",
            "slots": slots,
            "task_created": True,
        })

        return "TASK_CONFIRMED", "Confirm task is successfully created and ask if anything else is needed."

    # ---------------- STATUS CHECK ----------------
    if intent == "task_status":
        if ctx["task_created"]:
            update_context(call_sid, {"state": "POST_TASK"})
            return "POST_TASK", "Tell user task is completed and ask if anything else is needed."

    # ---------------- POST TASK ----------------
    if state == "POST_TASK":
        update_context(call_sid, {"state": "END_CALL", "call_completed": True})
        return "END_CALL", "Politely end the call."

    # ---------------- DEFAULT ----------------
    return state, "Respond naturally and ask if anything else is needed."