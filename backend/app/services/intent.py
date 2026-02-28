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

AFFIRM_PATTERN = re.compile(r"\b(yes|yeah|yep|sure|okay|ok)\b", re.I)


# =====================================================
# ENTITY PATTERNS
# =====================================================

NAME_PHRASE_PATTERN = re.compile(
    r"(?:my\s+(?:full\s+)?name\s+is\s+)([a-zA-Z\s]{2,40})",
    re.I,
)

DATE_PATTERN = re.compile(
    r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
    r"\d{1,2}(?:st|nd|rd|th)?\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)|"
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?)"
    r"\b",
    re.I,
)

TIME_PATTERN = re.compile(
    r"\b(\d{1,2}:\d{2}\s?(?:am|pm)?|"
    r"\d{1,2}\s?(?:am|pm)|"
    r"morning|afternoon|evening|night|noon|midnight|"
    r"\d{1,2}\s+o'?clock)\b",
    re.I,
)

HOURS_PATTERN = re.compile(
    r"\b(hours|timings|open|close)\b",
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


CALLBACK_PATTERN = re.compile(
    r"\b(call\s*back|callback|call\s+me\s+back|call\s+me|ring\s+me)\b", re.I
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

    if time_match:
        entities["callback_time"] = time_match.group(1).lower()

    return entities


# =====================================================
# INTENT DETECTION
# =====================================================

def detect_intent(call_sid: str, text: str) -> str:
    ctx = get_context(call_sid)
    state = ctx.get("state")
    clean_text = text.lower()

    # 1. EXIT MUST BE FIRST - Priority 1
    if EXIT_PATTERN.search(clean_text) or clean_text in ["no", "no thanks", "stop"]:
        return "exit"

    # 2. GLOBAL FAQS - Priority 2
    if HOURS_PATTERN.search(clean_text):
        return "clinic_hours"

    # 3. STATE LOCK - Only for slot filling
    # If we are in the middle of booking, and NOT exiting/asking hours, stay in booking
    if state and state.startswith("APPOINTMENT"):
        return "appointment"

    # 4. NEW INTENTS
    if APPOINTMENT_PATTERN.search(clean_text):
        return "appointment"
    if REFILL_PATTERN.search(clean_text):
        return "refill"
    if AFFIRM_PATTERN.search(clean_text):
        return "affirm"
    if CALLBACK_PATTERN.search(clean_text):
        return "callback"

    return "other"

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