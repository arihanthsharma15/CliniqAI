from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any


logger = logging.getLogger(__name__)


def _default_context() -> dict[str, Any]:
    return {
        "turn_count": 0,
        "failed_turns": 0,
        "ai_failures": 0,
        "appointment_confirmed": False,
        "no_speech_count": 0,
        "stt_mode": "deepgram",
        "last_user_text": "",
        "last_bot_text": "",
        "bot_repeat_count": 0,
        "deepgram_transcripts": {
            "final": "",
            "latest": "",
            "best": "",
        },
    }


CALL_CONTEXT: dict[str, dict[str, Any]] = {}


def get_context(call_sid: str) -> dict[str, Any]:
    call_sid = (call_sid or "").strip()
    if not call_sid:
        return _default_context()
    context = CALL_CONTEXT.get(call_sid)
    if context is None:
        context = _default_context()
        CALL_CONTEXT[call_sid] = context
    return context


def update_context(call_sid: str, updates: dict[str, Any]) -> None:
    call_sid = (call_sid or "").strip()
    if not call_sid:
        return
    context = get_context(call_sid)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(context.get(key), dict):
            merged = deepcopy(context.get(key))
            merged.update(value)
            context[key] = merged
        else:
            context[key] = value


def cleanup_context(call_sid: str) -> None:
    call_sid = (call_sid or "").strip()
    if not call_sid:
        return
    CALL_CONTEXT.pop(call_sid, None)
    logger.info("context_cleanup call_sid=%s", call_sid)


def increment_turn(call_sid: str) -> int:
    context = get_context(call_sid)
    current = int(context.get("turn_count", 0)) + 1
    context["turn_count"] = current
    return current


def should_end_demo(call_sid: str, max_turns: int) -> bool:
    if max_turns <= 0:
        return False
    context = get_context(call_sid)
    return int(context.get("turn_count", 0)) >= max_turns


def log_conversation_quality(call_sid: str, user_text: str, bot_text: str) -> None:
    context = get_context(call_sid)
    user = (user_text or "").strip().lower()
    bot = (bot_text or "").strip().lower()

    last_user = str(context.get("last_user_text", "")).strip().lower()
    last_bot = str(context.get("last_bot_text", "")).strip().lower()
    repeat_count = int(context.get("bot_repeat_count", 0))

    if bot and bot == last_bot:
        repeat_count += 1
    else:
        repeat_count = 0

    if repeat_count >= 1:
        logger.warning("conversation_flag type=possible_bot_loop who=bot")

    if user and last_user and user == last_user:
        logger.warning("conversation_flag type=possible_misheard_or_repeat who=user")

    if bot and user and bot != last_bot and user != last_user:
        logger.info("conversation_flag type=smooth true")

    context["last_user_text"] = user_text or ""
    context["last_bot_text"] = bot_text or ""
    context["bot_repeat_count"] = repeat_count
