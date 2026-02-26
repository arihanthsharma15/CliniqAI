from __future__ import annotations

from typing import Tuple
from app.services.context import get_context, update_context


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def _missing_slot(slots: dict):
    if not slots.get("name"):
        return "name"
    if not slots.get("appointment_type"):
        return "appointment_type"
    if not slots.get("date"):
        return "date"
    if not slots.get("time"):
        return "time"
    return None


# --------------------------------------------------
# MAIN STATE MACHINE
# --------------------------------------------------

def next_state(call_sid: str, intent: str, entities: dict) -> Tuple[str, str]:
    """
    Returns:
        (state, instruction_for_llm)
    """

    ctx = get_context(call_sid)
    state = ctx.get("state", "GREETING")
    slots = ctx.get("slots") or {}

    # --------------------------------------------------
    # LOCK INTO APPOINTMENT FLOW
    # --------------------------------------------------
    if state.startswith("APPOINTMENT"):
        intent = "appointment"

    # --------------------------------------------------
    # GREETING → GENERAL
    # --------------------------------------------------
    if state == "GREETING":
        update_context(call_sid, {"state": "GENERAL"})
        return "GENERAL", "Respond naturally and ask how you can help."
    

    # --------------------------------------------------
    # STATUS CHECK (WORKS ANYTIME)
    # --------------------------------------------------
    if intent == "task_status":
        if ctx.get("task_created"):
            update_context(call_sid, {"state": "POST_TASK"})
            return (
                "POST_TASK",
                "Inform user their request is already processed and ask if anything else is needed."
            )
        return (
            "GENERAL",
            "Tell user no active request exists and ask how you can help."
        )

    # --------------------------------------------------
    # POST TASK MODE (RECEPTIONIST MODE ⭐)
    # --------------------------------------------------
    if state == "POST_TASK":

        if intent == "task_status":
            return (
                "POST_TASK",
                "Confirm appointment request is registered and ask if anything else is needed."
            )

        if intent == "general":
            return (
                "POST_TASK",
                "Answer the question briefly and ask if anything else is needed."
            )

        if intent == "appointment":
            update_context(call_sid, {"task_created": False})
            return "GENERAL", "Start a new appointment request."

        if intent == "refill":
            update_context(
                call_sid,
                {
                    "task_created": False,
                    "state": "APPOINTMENT_NAME",
                    "slots": {"task_type": "refill"},
                },
            )
            return "APPOINTMENT_NAME", "Ask patient for full name."

        if intent == "exit":
            update_context(call_sid, {"state": "END_CALL"})
            return "END_CALL", "Thank the caller and say goodbye."

        return "POST_TASK", "Ask politely if anything else is needed."

    # --------------------------------------------------
    # GENERAL QUESTIONS
    # --------------------------------------------------
    if intent == "general" and state == "GENERAL":
        return (
            "GENERAL",
            "Answer briefly and ask if anything else is needed."
        )

    # --------------------------------------------------
    # START NEW TASK
    # --------------------------------------------------
    if (
        intent in ["appointment", "refill"]
        and not ctx.get("task_created")
        and state == "GENERAL"
    ):
        slots["task_type"] = intent

        update_context(
            call_sid,
            {
                "state": "APPOINTMENT_NAME",
                "slots": slots,
            },
        )

        return "APPOINTMENT_NAME", "Ask patient for full name."

    # --------------------------------------------------
    # SLOT FILLING FLOW
    # --------------------------------------------------
    if state.startswith("APPOINTMENT"):

        # fill slots automatically
        for k in ["name", "date", "time", "appointment_type"]:
            if entities.get(k):
                slots[k] = entities[k]

        update_context(call_sid, {"slots": slots})

        missing = _missing_slot(slots)

        # ---- NAME ----
        if missing == "name":
            update_context(call_sid, {"state": "APPOINTMENT_NAME"})
            return "APPOINTMENT_NAME", "Ask patient for full name only."

        # ---- TYPE ----
        if missing == "appointment_type":
            update_context(call_sid, {"state": "APPOINTMENT_TYPE"})
            return "APPOINTMENT_TYPE", "Ask what type of appointment they need."

        # ---- DATE ----
        if missing == "date":
            update_context(call_sid, {"state": "APPOINTMENT_DATE"})
            return "APPOINTMENT_DATE", "Ask preferred appointment date."

        # ---- TIME ----
        if missing == "time":
            update_context(call_sid, {"state": "APPOINTMENT_TIME"})
            return "APPOINTMENT_TIME", "Ask preferred appointment time."

        # --------------------------------------------------
        # ALL SLOTS FILLED → TASK CREATED
        # --------------------------------------------------
        update_context(
            call_sid,
            {
                "state": "POST_TASK",
                "slots": slots,
                "task_created": True,
            },
        )

        return (
            "POST_TASK",
            "Confirm appointment request is submitted and ask if anything else is needed."
        )

    # --------------------------------------------------
    # END CALL
    # --------------------------------------------------
    if state == "END_CALL":
        return "END_CALL", "Thank the caller and say goodbye."

    # --------------------------------------------------
    # DEFAULT FALLBACK
    # --------------------------------------------------
    return state, "Respond naturally and ask if anything else is needed."