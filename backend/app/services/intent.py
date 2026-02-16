import re

# Intent detection for routing tasks to staff vs doctor

APPOINTMENT_PATTERN = re.compile(r"\b(appointment|schedule|reschedule|book)\b", re.IGNORECASE)
GENERAL_PATTERN = re.compile(r"\b(hours|open|close|location|address|insurance)\b", re.IGNORECASE)
CALLBACK_PATTERN = re.compile(r"\b(call me back|callback|call back)\b", re.IGNORECASE)
MEDICATION_REFILL_PATTERN = re.compile(
    r"\b(medication refill|medicine refill|prescription refill|refill prescription|refill medicine)\b",
    re.IGNORECASE,
)

INTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("appointment_scheduling", APPOINTMENT_PATTERN),
    ("general_question", GENERAL_PATTERN),
    ("callback_request", CALLBACK_PATTERN),
    ("medication_refill", MEDICATION_REFILL_PATTERN),
]


def infer_request_type(text: str) -> str:
    for request_type, pattern in INTENT_PATTERNS:
        if pattern.search(text):
            return request_type
    return "other"


def infer_request_types(text: str) -> list[str]:
    found: list[str] = []
    for request_type, pattern in INTENT_PATTERNS:
        if pattern.search(text):
            found.append(request_type)
    if not found:
        found.append("other")
    return found
