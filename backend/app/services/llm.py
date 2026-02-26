from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings


from app.services.intent import parse_user_input
from app.services.state_machine import next_state
from app.services.context import get_context, update_context


SYSTEM_PROMPT = (
    "You are a clinic voice assistant.\n"
    "Follow backend instructions strictly.\n"
    "Never decide conversation flow yourself.\n"
    "Speak naturally, briefly (under 20 words).\n"
    "Do NOT ask extra questions unless instructed.\n"
)


@dataclass
class LLMReply:
    text: str
    openai_status: str
    fallback_used: bool


def _extract_text(data: dict[str, Any]) -> str:
    text = data.get("output_text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    for item in data.get("output", []) if isinstance(data.get("output"), list) else []:
        for block in item.get("content", []) if isinstance(item, dict) else []:
            if isinstance(block, dict) and block.get("type") in {"output_text", "text"}:
                val = block.get("text")
                if isinstance(val, str) and val.strip():
                    return val.strip()
    return ""


def _extract_chat_completion_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list):
        return ""
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        msg = choice.get("message")
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return ""


def _rule_based_reply(user_text: str, context: dict[str, Any] | None = None) -> str:
    """
    Safety fallback when LLM provider fails.
    Does NOT control conversation anymore.
    """
    return "I'm having a small connection issue. Let me assist you shortly."


def _call_openai(user_text: str, context: dict[str, Any] | None = None) -> LLMReply:
    if not settings.openai_api_key:
        return LLMReply(_rule_based_reply(user_text, context), "missing_openai_api_key", True)
    payload: dict[str, Any] = {
        "model": settings.openai_model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": SYSTEM_PROMPT}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
        ],
        "max_output_tokens": 120,
    }
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=httpx.Timeout(connect=2.5, read=6.0, write=3.0, pool=2.0)) as client:
            resp = client.post("https://api.openai.com/v1/responses", headers=headers, json=payload)
            resp.raise_for_status()
            text = _extract_text(resp.json())
            if text:
                return LLMReply(text, "openai_ok", False)
    except Exception:
        pass
    return LLMReply(_rule_based_reply(user_text, context), "openai_fallback", True)


def _call_groq(user_text: str, context: dict[str, Any] | None = None) -> LLMReply:
    if not settings.groq_api_key:
        return LLMReply(_rule_based_reply(user_text, context), "missing_groq_api_key", True)
    payload: dict[str, Any] = {
        "model": settings.groq_model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_text}],
        "max_tokens": 120,
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=httpx.Timeout(connect=2.5, read=6.0, write=3.0, pool=2.0)) as client:
            resp = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            text = _extract_chat_completion_text(resp.json())
            if text:
                return LLMReply(text, "groq_ok", False)
    except Exception:
        pass
    return LLMReply(_rule_based_reply(user_text, context), "groq_fallback", True)


def generate_controlled_reply(call_sid: str, user_text: str) -> LLMReply:
    """
    MAIN ENTRYPOINT FOR VOICE AGENT
    State machine controls conversation.
    LLM only verbalizes instruction.
    """

    context = get_context(call_sid)

    # -------- Intent + Entities ----------
    parsed = parse_user_input(call_sid, user_text)
    intent = parsed["intent"]
    entities = parsed["entities"]
    # ⭐ merge entities detected earlier in calls.py
    context_entities = context.get("latest_entities", {})
    entities = {**context_entities, **entities}
    print("Entities: ", entities)



    # -------- STATE MACHINE ----------
    state, instruction = next_state(call_sid, intent, entities)
    print("STATE:", state)
    print("ENTITIES:", entities)

    # store latest state
    update_context(call_sid, {"state": state})

    # -------- LLM SPEAKS INSTRUCTION ----------
    guided_text = instruction

    provider = settings.llm_provider.strip().lower()

    if provider == "groq":
        reply = _call_groq(guided_text, context)
    elif provider == "openai":
        reply = _call_openai(guided_text, context)
    else:
        reply = _call_groq(guided_text, context)
        if reply.fallback_used:
            reply = _call_openai(guided_text, context)

    return reply


def generate_call_reply(user_text: str, context: dict[str, Any] | None = None) -> LLMReply:
    if not user_text.strip():
        return LLMReply("Please tell me your request.", "empty_input", True)

    provider = settings.llm_provider.strip().lower()
    if provider not in {"auto", "groq", "openai"}:
        provider = "auto"
    if provider == "groq":
        return _call_groq(user_text, context)
    if provider == "openai":
        return _call_openai(user_text, context)

    if settings.groq_api_key:
        groq_reply = _call_groq(user_text, context)
        if not groq_reply.fallback_used:
            return groq_reply
        if settings.openai_api_key:
            openai_reply = _call_openai(user_text, context)
            if not openai_reply.fallback_used:
                return openai_reply
        return groq_reply
    if settings.openai_api_key:
        return _call_openai(user_text, context)
    return LLMReply(_rule_based_reply(user_text, context), "missing_llm_api_key", True)
