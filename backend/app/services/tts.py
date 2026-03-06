from __future__ import annotations

import base64
import logging
from pathlib import Path
from time import time
from typing import Any
from uuid import uuid4

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account

from app.core.config import settings


AUDIO_CACHE: dict[str, tuple[bytes, float]] = {}
CACHE_TTL_SECONDS = 900
GOOGLE_TTS_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
logger = logging.getLogger(__name__)
CACHE_DIR = Path("/tmp/tts_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cleanup_cache() -> None:
    now = time()
    stale = [k for k, (_, ts) in AUDIO_CACHE.items() if now - ts > CACHE_TTL_SECONDS]
    for key in stale:
        AUDIO_CACHE.pop(key, None)



def cache_audio(audio_bytes: bytes) -> str:
    _cleanup_cache()
    audio_id = uuid4().hex
    AUDIO_CACHE[audio_id] = (audio_bytes, time())

    file_path = CACHE_DIR / f"{audio_id}.mp3"
    file_path.write_bytes(audio_bytes)

    return audio_id


def get_cached_audio(audio_id: str) -> bytes | None:
    _cleanup_cache()

    item = AUDIO_CACHE.get(audio_id)
    if item:
        return item[0]

    file_path = CACHE_DIR / f"{audio_id}.mp3"
    if file_path.exists():
        return file_path.read_bytes()

    return None


def synthesize_elevenlabs(text: str) -> bytes | None:
    if not text.strip():
        return None
    if settings.tts_provider.strip().lower() != "elevenlabs":
        return None
    if not settings.elevenlabs_api_key or not settings.elevenlabs_voice_id:
        return None

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}"
    payload: dict[str, Any] = {
        "text": text[:1200],
        "model_id": settings.elevenlabs_model_id,
    }
    headers = {
        "xi-api-key": settings.elevenlabs_api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }

    with httpx.Client(timeout=12.0) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.content


def synthesize_google_tts(text: str) -> bytes | None:
    if not text.strip():
        return None
    if settings.tts_provider.strip().lower() != "google":
        return None

    import json
    import os

    # Production: load from base64 env var
    creds_base64 = os.environ.get("GOOGLE_TTS_CREDENTIALS_BASE64", "").strip()
    if creds_base64:
        try:
            creds_dict = json.loads(base64.b64decode(creds_base64).decode("utf-8"))
            logger.info("Loaded Google TTS credentials from BASE64 env var")
            credentials = service_account.Credentials.from_service_account_info(
                creds_dict,
                scopes=[GOOGLE_TTS_SCOPE],
            )
        except Exception as e:
            logger.error("Failed to parse GOOGLE_TTS_CREDENTIALS_BASE64: %s", str(e), exc_info=True)
            return None
    else:
        # Local dev: load from file path
        creds_path = (settings.google_tts_credentials_path or "").strip()
        if not creds_path:
            logger.error("No GOOGLE_TTS_CREDENTIALS_BASE64 env var and no google_tts_credentials_path config")
            return None
        cred_file = Path(creds_path)
        if not cred_file.exists():
            logger.error("Google TTS credentials file not found: %s", creds_path)
            return None
        try:
            credentials = service_account.Credentials.from_service_account_file(
                str(cred_file),
                scopes=[GOOGLE_TTS_SCOPE],
            )
        except Exception as e:
            logger.error("Failed to load credentials from file: %s", str(e), exc_info=True)
            return None

    try:
        credentials.refresh(Request())
        if not credentials.token:
            logger.error("Failed to get credentials token")
            return None
    except Exception as e:
        logger.error("Failed to refresh credentials: %s", str(e), exc_info=True)
        return None

    headers = {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "input": {"text": text[:2000]},
        "voice": {
            "languageCode": settings.google_tts_language_code,
            "name": settings.google_tts_voice_name,
        },
        "audioConfig": {
            "audioEncoding": "MP3",
        },
    }

    try:
        with httpx.Client(timeout=12.0) as client:
            resp = client.post("https://texttospeech.googleapis.com/v1/text:synthesize", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            logger.info("Google TTS synthesis successful: %d bytes", len(data.get("audioContent", "")))

        audio_b64 = data.get("audioContent")
        if not isinstance(audio_b64, str) or not audio_b64:
            logger.error("No audioContent in Google TTS response")
            return None
        return base64.b64decode(audio_b64)
    except Exception as e:
        logger.error("Google TTS API call failed: %s", str(e), exc_info=True)
        return None


def synthesize_tts(text: str) -> bytes | None:
    provider = settings.tts_provider.strip().lower()
    if provider == "elevenlabs":
        return synthesize_elevenlabs(text)
    if provider == "google":
        return synthesize_google_tts(text)
    return None
