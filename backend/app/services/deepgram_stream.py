from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Callable

import websockets
from websockets.exceptions import ConnectionClosed


logger = logging.getLogger(__name__)


class DeepgramRealtimeBridge:
    def __init__(
        self,
        api_key: str,
        on_transcript: Callable[[str, str, bool], None],
        call_sid_hint: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._on_transcript = on_transcript
        self._call_sid = call_sid_hint or ""
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._reader_task: asyncio.Task | None = None
        self._media_frames = 0

    @property
    def call_sid(self) -> str:
        return self._call_sid

    async def connect(self) -> None:
        url = (
            "wss://api.deepgram.com/v1/listen"
            "?encoding=mulaw&sample_rate=8000&channels=1&interim_results=true&endpointing=300"
        )
        self._ws = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Token {self._api_key}"},
            ping_interval=20,
            ping_timeout=20,
        )
        self._reader_task = asyncio.create_task(self._reader())

    async def _reader(self) -> None:
        if not self._ws:
            return
        try:
            async for raw in self._ws:
                if not isinstance(raw, str):
                    continue
                self._handle_deepgram_message(raw)
        except ConnectionClosed:
            logger.info("deepgram websocket closed call_sid=%s", self._call_sid or "unknown")
        except Exception as exc:
            logger.error("deepgram reader failed call_sid=%s error=%s", self._call_sid or "unknown", str(exc))

    def _handle_deepgram_message(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if payload.get("type") != "Results":
            return
        channel = payload.get("channel", {})
        alternatives = channel.get("alternatives", [])
        if not alternatives:
            return
        transcript = str(alternatives[0].get("transcript", "")).strip()
        if not transcript:
            return
        is_final = bool(payload.get("is_final")) or bool(payload.get("speech_final"))
        if self._call_sid:
            self._on_transcript(self._call_sid, transcript, is_final)

    async def ingest_twilio_message(self, twilio_raw: str) -> None:
        if not self._ws:
            return
        try:
            payload = json.loads(twilio_raw)
        except json.JSONDecodeError:
            return

        event = payload.get("event")
        if event == "start":
            start = payload.get("start", {})
            self._call_sid = str(start.get("callSid") or self._call_sid)
            logger.warning("twilio_stream start")
            return
        if event == "media":
            media = payload.get("media", {})
            b64 = media.get("payload")
            if not isinstance(b64, str) or not b64:
                return
            try:
                audio = base64.b64decode(b64)
            except Exception:
                return
            await self._ws.send(audio)
            self._media_frames += 1
            return
        if event == "stop":
            logger.warning("twilio_stream stop total_frames=%s", self._media_frames)
            await self.close()

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
