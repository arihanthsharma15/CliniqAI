import re


APPOINTMENT_PATTERN = re.compile(r"appointment|schedule|reschedule|book", re.IGNORECASE)
GENERAL_PATTERN = re.compile(r"hours|open|close|location|address|insurance", re.IGNORECASE)
CALLBACK_PATTERN = re.compile(r"call me back|callback|call back", re.IGNORECASE)


def infer_request_type(text: str) -> str:
    if APPOINTMENT_PATTERN.search(text):
        return "appointment_scheduling"
    if GENERAL_PATTERN.search(text):
        return "general_question"
    if CALLBACK_PATTERN.search(text):
        return "callback_request"
    return "other"


def infer_request_types(text: str) -> list[str]:
    found: list[str] = []
    if APPOINTMENT_PATTERN.search(text):
        found.append("appointment_scheduling")
    if GENERAL_PATTERN.search(text):
        found.append("general_question")
    if CALLBACK_PATTERN.search(text):
        found.append("callback_request")
    if not found:
        found.append("other")
    return found
