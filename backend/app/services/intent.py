from __future__ import annotations

import re
from typing import Dict, Any, List


# -----------------------------
# INTENT PATTERNS
# -----------------------------

APPOINTMENT_PATTERN = re.compile(
    r"\b(appointment|schedule|reschedule|book)\b", re.IGNORECASE
)

REFILL_PATTERN = re.compile(
    r"\b(refill|prescription refill|medicine refill)\b", re.IGNORECASE
)

GENERAL_PATTERN = re.compile(
    r"\b(hours|open|close|location|address|insurance)\b", re.IGNORECASE
)

STATUS_PATTERN = re.compile(
    r"\b(is it done|my appointment done|status|completed|is my task done)\b",
    re.IGNORECASE,
)


# -----------------------------
# ENTITY EXTRACTION
# -----------------------------

NAME_PATTERN = re.compile(r"\bmy name is ([a-zA-Z]+)", re.IGNORECASE)

DATE_PATTERN = re.compile(
    r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)

TIME_PATTERN = re.compile(
    r"\b(\d{1,2}\s?(?:am|pm))\b",
    re.IGNORECASE,
)


def extract_entities(text: str) -> Dict[str, Any]:
    entities: Dict[str, Any] = {}

    name_match = NAME_PATTERN.search(text)
    if name_match:
        entities["name"] = name_match.group(1)

    date_match = DATE_PATTERN.search(text)
    if date_match:
        entities["date"] = date_match.group(1)

    time_match = TIME_PATTERN.search(text)
    if time_match:
        entities["time"] = time_match.group(1)

    return entities


# -----------------------------
# INTENT CLASSIFICATION
# -----------------------------

def detect_intent(text: str) -> str:
    if STATUS_PATTERN.search(text):
        return "task_status"

    if APPOINTMENT_PATTERN.search(text):
        return "appointment"

    if REFILL_PATTERN.search(text):
        return "refill"

    if GENERAL_PATTERN.search(text):
        return "general"

    return "general"


# -----------------------------
# MULTI INTENT SUPPORT
# -----------------------------

def detect_intents(text: str) -> List[str]:
    intents = []

    if STATUS_PATTERN.search(text):
        intents.append("task_status")

    if APPOINTMENT_PATTERN.search(text):
        intents.append("appointment")

    if REFILL_PATTERN.search(text):
        intents.append("refill")

    if GENERAL_PATTERN.search(text):
        intents.append("general")

    if not intents:
        intents.append("general")

    return intents


# -----------------------------
# MAIN PIPELINE FUNCTION ⭐
# -----------------------------

def parse_user_input(text: str) -> Dict[str, Any]:
    """
    Standard interface used by state machine.
    """

    intent = detect_intent(text)
    entities = extract_entities(text)

    return {
        "intent": intent,
        "entities": entities,
    }