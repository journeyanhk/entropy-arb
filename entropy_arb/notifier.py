"""Telegram alerting for engine-level events (HALT, hedge failure, drift
sentinel, liquidation risk).

Non-blocking by construction: send() only enqueues; a single worker task
posts messages through the shared aiohttp session. Failures are logged and
retried once, never raised into the engine. With no credentials the notifier
is a silent no-op, so --record-only / dev runs need no Telegram setup.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import aiohttp

log = logging.getLogger("notifier")

TG_API = "https://api.telegram.org/bot{token}/sendMessage"
POST_TIMEOUT = 10.0
RETRY_DELAY_SEC = 2.0


class Notifier:
    def __init__(self, token: Optional[str], chat_id: Optional[str],
                 max_queue: int = 64) -> None:
        self.token = token or ""
        self.chat_id = chat_id or ""
        self.enabled = bool(self.token and self.chat_id)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self._done = False
        self._session: Optional[aiohttp.ClientSession] = None

    @classmethod
    def from_env(cls) -> "Notifier":
        return cls(os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID"))

    def send(self, text: str) -> None:
        """Enqueue an alert (never blocks, never raises)."""
        if not self.enabled or self._done:
            return
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            log.warning("notifier queue full — dropping alert: %.80s", text)

    async def run(self, session: aiohttp.ClientSession) -> None:
        """Worker: drain the queue until close() is called."""
        self._session = session
        while not self._done:
            try:
                text = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            await self._post(session, text)

    async def close(self) -> None:
        """Stop the worker and drain a few queued alerts best-effort."""
        self._done = True
        if not self.enabled or self._session is None:
            return
        for _ in range(3):
            try:
                text = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await self._post(self._session, text)
            except Exception:
                pass

    async def _post(self, session: aiohttp.ClientSession, text: str) -> None:
        url = TG_API.format(token=self.token)
        body = {"chat_id": self.chat_id, "text": text}
        for attempt in (1, 2):
            try:
                async with session.post(
                        url, json=body,
                        timeout=aiohttp.ClientTimeout(total=POST_TIMEOUT)) as r:
                    if r.status == 200:
                        return
                    resp = await r.text()
                    log.warning("telegram HTTP %d: %.120s", r.status, resp)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("telegram send failed (attempt %d): %r",
                            attempt, e)
            if attempt == 1:
                await asyncio.sleep(RETRY_DELAY_SEC)