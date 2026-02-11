from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any

import httpx

from app.core.config import settings
from app.services.intent import infer_request_type


SYSTEM_PROMPT = (
    "You are a helpful phone assistant for a medical clinic. "
    "Speak clearly and briefly. Ask one follow-up question when details are missing. "
    "Do not provide diagnosis. If urgent symptoms are mentioned, advise calling emergency services."
)


logger = logging.getLogger(__name__)


@dataclass
class LLMReply:
    text: str
    openai_status: str
    fallback_used: bool


def _extract_output_text(data: dict[str, Any]) -> str:
    # Newer Responses API payloads include output_text directly.
    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    # Fallback: parse output content blocks.
    output = data.get("output")
    if isinstance(output, list):
        texts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") in {"output_text", "text"}:
                    text_val = block.get("text")
                    if isinstance(text_val, str) and text_val.strip():
                        texts.append(text_val.strip())
        if texts:
            return " ".join(texts)

    return ""


def _extract_chat_completion_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list):
        return ""
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _rule_based_reply(user_text: str) -> str:
    lowered = user_text.lower()
    request_type = infer_request_type(user_text)

    if re.search(r"\b(hours|open|close|location|address|insurance)\b", lowered):
        return (
            "I can help with that. Please tell me which clinic location you mean, "
            "and I will note your question for staff confirmation."
        )
    if request_type == "appointment_scheduling":
        has_name = bool(re.search(r"\bmy name is\b", lowered))
        has_time = bool(re.search(r"\b(am|pm|morning|afternoon|evening|\d{1,2}(:\d{2})?)\b", lowered))
        has_date = bool(
            re.search(
                r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next)\b",
                lowered,
            )
        )
        if has_name and has_time:
            return "Perfect. I have your name and preferred time. I will share this with the clinic staff."
        if has_date and has_time:
            return "Understood. I have your preferred date and time. Please share your full name."
        if has_time:
            return (
                "Got it, I noted your preferred time. "
                "Please tell me your full name so I can include it."
            )
        return (
            "Sure, I can help with scheduling. "
            "Please share your preferred date, time, and your full name."
        )
    if request_type == "callback_request":
        if re.search(r"\b(evening|morning|afternoon|am|pm|\d{1,2}(:\d{2})?)\b", lowered):
            return "Understood. I have your callback request and preferred time. Our staff will follow up."
        return "Okay, I can arrange a callback. Please confirm the best time window."
    return "I understand. Please share one more detail so I can capture your request correctly."


def _call_openai(user_text: str) -> LLMReply:
    if not settings.openai_api_key:
        return LLMReply(
            text=_rule_based_reply(user_text),
            openai_status="missing_openai_api_key",
            fallback_used=True,
        )

    payload: dict[str, Any] = {
        "model": settings.openai_model,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": SYSTEM_PROMPT}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_text}]},
        ],
        "max_output_tokens": 120,
    }
    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }

    try:
        timeout = httpx.Timeout(connect=2.5, read=6.0, write=3.0, pool=2.0)
        with httpx.Client(timeout=timeout) as client:
            resp = client.post("https://api.openai.com/v1/responses", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        body = exc.response.text[:300]
        logger.error("OpenAI API error status=%s body=%s", status, body)
        return LLMReply(
            text=_rule_based_reply(user_text),
            openai_status=f"openai_http_{status}",
            fallback_used=True,
        )
    except Exception as exc:
        logger.error("OpenAI request failed: %s", str(exc))
        return LLMReply(
            text=_rule_based_reply(user_text),
            openai_status="openai_request_exception",
            fallback_used=True,
        )

    parsed_text = _extract_output_text(data)
    if parsed_text:
        return LLMReply(text=parsed_text, openai_status="openai_ok", fallback_used=False)

    return LLMReply(
        text=_rule_based_reply(user_text),
        openai_status="openai_empty_output",
        fallback_used=True,
    )


def _call_groq(user_text: str) -> LLMReply:
    if not settings.groq_api_key:
        return LLMReply(
            text=_rule_based_reply(user_text),
            openai_status="missing_groq_api_key",
            fallback_used=True,
        )

    payload: dict[str, Any] = {
        "model": settings.groq_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "max_tokens": 120,
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }

    try:
        timeout = httpx.Timeout(connect=2.5, read=6.0, write=3.0, pool=2.0)
        with httpx.Client(timeout=timeout) as client:
            resp = client.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        body = exc.response.text[:300]
        logger.error("Groq API error status=%s body=%s", status, body)
        return LLMReply(
            text=_rule_based_reply(user_text),
            openai_status=f"groq_http_{status}",
            fallback_used=True,
        )
    except Exception as exc:
        logger.error("Groq request failed: %s", str(exc))
        return LLMReply(
            text=_rule_based_reply(user_text),
            openai_status="groq_request_exception",
            fallback_used=True,
        )

    parsed_text = _extract_chat_completion_text(data)
    if parsed_text:
        return LLMReply(text=parsed_text, openai_status="groq_ok", fallback_used=False)

    return LLMReply(
        text=_rule_based_reply(user_text),
        openai_status="groq_empty_output",
        fallback_used=True,
    )


def generate_call_reply(user_text: str) -> LLMReply:
    if not user_text.strip():
        return LLMReply(
            text="I am here. Please tell me your request when you are ready.",
            openai_status="empty_input",
            fallback_used=True,
        )

    provider = settings.llm_provider.strip().lower()
    if provider not in {"auto", "groq", "openai"}:
        provider = "auto"

    if provider == "groq":
        return _call_groq(user_text)
    if provider == "openai":
        return _call_openai(user_text)

    # Auto: prefer Groq (free-tier friendly), then OpenAI.
    if settings.groq_api_key:
        groq_reply = _call_groq(user_text)
        if not groq_reply.fallback_used:
            return groq_reply
        if settings.openai_api_key:
            openai_reply = _call_openai(user_text)
            if not openai_reply.fallback_used:
                return openai_reply
        return groq_reply

    if settings.openai_api_key:
        return _call_openai(user_text)

    return LLMReply(
        text="I understood your request. Could you share a bit more detail so I can help correctly?",
        openai_status="missing_llm_api_key",
        fallback_used=True,
    )
