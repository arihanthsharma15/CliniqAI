from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Tuple
from typing import Any
import httpx
from app.core.config import settings
from app.services.intent import parse_user_input
from app.services.state_machine import next_state
from app.services.context import get_context, update_context


SYSTEM_PROMPT = (
    "You are a clinical receptionist bot. Follow the BACKEND INSTRUCTION exactly.\n"
    "1. NEVER make up clinic hours, policies, or rules that were not given to you.\n"
    "2. NEVER reject or comment on dates or times the patient provides.\n"
    "3. Keep every response under 15 words.\n"
    "4. If instruction says STRICT, output ONLY what the instruction says, word for word.\n"
    "5. Do not add any information, rules, or policies on your own."
)

@dataclass
class LLMReply:
    text: str
    openai_status: str
    fallback_used: bool

def _extract_chat_completion_text(data: dict[str, Any]) -> str:
    choices = data.get("choices", [])
    if choices and isinstance(choices[0], dict):
        return choices[0].get("message", {}).get("content", "").strip()
    return ""

def _call_groq_unified(instruction: str, user_text: str, history: str) -> LLMReply:
    if not settings.groq_api_key:
        return LLMReply("I'm having a connection issue.", "missing_api_key", True)

    #  This prompt solves the loop by giving the LLM the full picture
    unified_content = (
    f"CONVERSATION HISTORY:\n{history}\n\n"
    f"USER JUST SAID: \"{user_text}\"\n\n"
    f"IMPORTANT BACKEND INSTRUCTION: {instruction}\n"
    "Follow the instruction above regardless of what the user just said."
    f"\n\nRESPONSE:"
)

    payload = {
        "model": settings.groq_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": unified_content}
        ],
        "max_tokens": 120,
        "temperature": 0.2
    }
    
    headers = {"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"}
    
    try:
        with httpx.Client(timeout=6.0) as client:
            resp = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            text = _extract_chat_completion_text(resp.json())
            if text:
                return LLMReply(text, "groq_ok", False)
    except Exception as e:
        print(f"LLM Error: {e}")
    
    return LLMReply("I'm sorry, can you repeat that?", "groq_fallback", True)

def generate_controlled_reply(call_sid: str, user_text: str) -> Tuple[LLMReply, str, dict]:
    """
    MAIN ENTRYPOINT: Merges state logic with LLM flexibility.
    """
    context = get_context(call_sid)
    
    # 1. Parse Intent & Entities
    parsed = parse_user_input(call_sid, user_text)
    intent = parsed["intent"]
    entities = {**context.get("latest_entities", {}), **parsed["entities"]}

    # 2. Get State Machine Direction
    state, instruction, updated_slots = next_state(call_sid, intent, entities, user_text)

    # 3. Fetch History (Stored in your DB/Transcript model)
    # We pull the current session transcript to give the LLM 'memory'
    from app.api.routes.calls import _get_or_create_transcript
    from app.db.session import SessionLocal
    
    history_text = ""
    with SessionLocal() as db:
        transcript_obj = _get_or_create_transcript(db, call_sid)
        history_text = transcript_obj.text if transcript_obj else ""

    # 4. Generate the smart reply
    reply = _call_groq_unified(instruction, user_text, history_text)
    return reply, state, updated_slots # <--- RETURN ALL THREE