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
        if has_name and has_time:
            return (
                "Understood. I have your preferred time and name. "
                "Do you also want to share the reason for the visit?"
            )
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
        return "Okay, I can arrange a callback. Please confirm the best number and time window."
    return "I understand. Please share one more detail so I can capture your request correctly."


def generate_call_reply(user_text: str) -> LLMReply:
    if not user_text.strip():
        return LLMReply(
            text="I am here. Please tell me your request when you are ready.",
            openai_status="empty_input",
            fallback_used=True,
        )

    if not settings.openai_api_key:
        return LLMReply(
            text="I understood your request. Could you share a bit more detail so I can help correctly?",
            openai_status="missing_api_key",
            fallback_used=True,
        )

    payload: dict[str, Any] = {
        "model": "gpt-4o-mini",
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
            openai_status=f"http_{status}",
            fallback_used=True,
        )
    except Exception as exc:
        logger.error("OpenAI request failed: %s", str(exc))
        return LLMReply(
            text=_rule_based_reply(user_text),
            openai_status="request_exception",
            fallback_used=True,
        )

    parsed_text = _extract_output_text(data)
    if parsed_text:
        return LLMReply(text=parsed_text, openai_status="ok", fallback_used=False)

    return LLMReply(
        text=_rule_based_reply(user_text),
        openai_status="empty_output",
        fallback_used=True,
    )
