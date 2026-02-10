import re

def infer_request_type(text: str) -> str:
    lowered = text.lower()
    if re.search(r"appointment|schedule|reschedule|book", lowered):
        return "appointment_scheduling"
    if re.search(r"hours|open|close|location|address|insurance", lowered):
        return "general_question"
    if re.search(r"call me back|callback|call back", lowered):
        return "callback_request"
    return "other"
