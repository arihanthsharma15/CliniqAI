from __future__ import annotations

import re
from typing import Dict, Any, List

from app.services.context import get_context


# =====================================================
# INTENT PATTERNS
# =====================================================

APPOINTMENT_PATTERN = re.compile(
    r"\b(appointment|schedule|reschedule|book)\b", re.I
)

REFILL_PATTERN = re.compile(
    r"\b(refill|prescription refill|medicine refill)\b", re.I
)

GENERAL_PATTERN = re.compile(
    r"\b(hours|open|close|location|address|insurance)\b", re.I
)

STATUS_PATTERN = re.compile(
    r"\b(is it done|status|completed|callback|did someone call)\b", re.I
)

EXIT_PATTERN = re.compile(
    r"\b(no|nothing|that's all|bye|goodbye|no thanks)\b", re.I
)


# =====================================================
# ENTITY PATTERNS
# =====================================================

NAME_PHRASE_PATTERN = re.compile(
    r"(?:my\s+(?:full\s+)?name\s+is\s+)([a-zA-Z\s]{2,40})",
    re.I,
)

DATE_PATTERN = re.compile(
    r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.I,
)

TIME_PATTERN = re.compile(
    r"\b(\d{1,2}\s?(?:am|pm)|morning|afternoon|evening|night)\b",
    re.I,
)

APPOINTMENT_TYPE_PATTERN = re.compile(
    r"\b("
    r"general(?:\s+appointment)?|"
    r"check(?:-?\s*up)?|"
    r"follow\s*up|"
    r"consultation|"
    r"physical|"
    r"vaccination|"
    r"doctor\s*visit"
    r")\b",
    re.I,
)


RESERVED_WORDS = {
    "today",
    "tomorrow",
    "morning",
    "afternoon",
    "evening",
    "night",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
}


# =====================================================
# ENTITY EXTRACTION
# =====================================================

def extract_entities(text: str) -> Dict[str, Any]:

    entities: Dict[str, Any] = {}
    text_clean = text.strip()

    # ---------- appointment type ----------
    type_match = APPOINTMENT_TYPE_PATTERN.search(text_clean)
    if type_match:
        entities["appointment_type"] = type_match.group(1).lower()

    # ---------- explicit name ----------
    name_phrase = NAME_PHRASE_PATTERN.search(text_clean)
    if name_phrase:
        entities["name"] = name_phrase.group(1).title()

    else:
        # standalone name reply
        stripped = text_clean.lower()

        if (
            re.fullmatch(r"[a-zA-Z]{2,}(?:\s+[a-zA-Z]{2,}){0,2}", stripped)
            and stripped not in RESERVED_WORDS
        ):
            entities["name"] = stripped.title()

    # ---------- date ----------
    date_match = DATE_PATTERN.search(text_clean)
    if date_match:
        entities["date"] = date_match.group(1).lower()

    # ---------- time ----------
    time_match = TIME_PATTERN.search(text_clean)
    if time_match:
        entities["time"] = time_match.group(1).lower()

    return entities


# =====================================================
# INTENT DETECTION
# =====================================================

def detect_intent(call_sid: str, text: str) -> str:

    ctx = get_context(call_sid)
    state = ctx.get("state")

    # LOCK conversation once appointment flow started
    if state and state.startswith("APPOINTMENT"):
        return "appointment"

    if EXIT_PATTERN.search(text):
        return "exit"

    if STATUS_PATTERN.search(text):
        return "task_status"

    if APPOINTMENT_PATTERN.search(text):
        return "appointment"

    if REFILL_PATTERN.search(text):
        return "refill"

    if GENERAL_PATTERN.search(text):
        return "general"

    # slot continuation
    if DATE_PATTERN.search(text) or TIME_PATTERN.search(text):
        return "appointment"

    return "other"


# =====================================================
# MULTI INTENT (optional future)
# =====================================================

def detect_intents(call_sid: str, text: str) -> List[str]:

    intents = []

    if EXIT_PATTERN.search(text):
        intents.append("exit")

    if STATUS_PATTERN.search(text):
        intents.append("task_status")

    if APPOINTMENT_PATTERN.search(text):
        intents.append("appointment")

    if REFILL_PATTERN.search(text):
        intents.append("refill")

    if GENERAL_PATTERN.search(text):
        intents.append("general")

    if not intents:
        intents.append("other")

    return intents


# =====================================================
# MAIN PIPELINE ⭐
# =====================================================

def parse_user_input(call_sid: str, text: str) -> Dict[str, Any]:

    intent = detect_intent(call_sid, text)
    entities = extract_entities(text)

    ctx = get_context(call_sid)
    state = ctx.get("state")

    # prevent name overwrite during appointment type step
    if state == "APPOINTMENT_TYPE":
        entities.pop("name", None)

    # continue slot filling
    if state and state.startswith("APPOINTMENT"):
        if entities:
            intent = "appointment"

    return {
        "intent": intent,
        "entities": entities,
    }


# =====================================================
# BACKWARD COMPATIBILITY
# =====================================================

def infer_request_type(call_sid: str, text: str) -> str:
    return parse_user_input(call_sid, text)["intent"]


def infer_request_types(call_sid: str, text: str) -> List[str]:
    return [parse_user_input(call_sid, text)["intent"]]