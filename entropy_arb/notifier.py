"""Alerting for engine-level events (HALT, hedge failure, drift sentinel,
liquidation risk) over multiple channels: Telegram and Server酱 (ServerChan).

Non-blocking by construction: send() only enqueues; a single worker task
posts messages through the shared aiohttp session. Failures are logged and
retried once, never raised into the engine. With no credentials configured
the notifier is a silent no-op, so --record-only / dev runs need no setup.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import List, Optional

import aiohttp

log = logging.getLogger("notifier")

TG_API = "https://api.telegram.org/bot{token}/sendMessage"
SC_API = "https://sctapi.ftqq.com/{sendkey}.send"
POST_TIMEOUT = 10.0
RETRY_DELAY_SEC = 2.0


class _Channel:
    name = "channel"
    enabled = False

    async def post(self, session: aiohttp.ClientSession, text: str) -> None:
        raise NotImplementedError


class TelegramChannel(_Channel):
    name = "telegram"

    def __init__(self, token: Optional[str], chat_id: Optional[str]) -> None:
        self.token = token or ""
        self.chat_id = chat_id or ""
        self.enabled = bool(self.token and self.chat_id)

    async def post(self, session: aiohttp.ClientSession, text: str) -> None:
        url = TG_API.format(token=self.token)
        body = {"chat_id": self.chat_id, "text": text}
        async with session.post(
                url, json=body,
                timeout=aiohttp.ClientTimeout(total=POST_TIMEOUT)) as r:
            if r.status != 200:
                resp = await r.text()
                log.warning("telegram HTTP %d: %.120s", r.status, resp)
                raise RuntimeError(f"telegram HTTP {r.status}")


class ServerChanChannel(_Channel):
    """Server酱 (https://sct.ftqq.com): one SendKey, no chat setup."""

    name = "serverchan"

    def __init__(self, sendkey: Optional[str]) -> None:
        self.sendkey = sendkey or ""
        self.enabled = bool(self.sendkey)

    async def post(self, session: aiohttp.ClientSession, text: str) -> None:
        url = SC_API.format(sendkey=self.sendkey)
        lines = text.splitlines()
        title = (lines[0] if lines else "entropy-arb")[:64]
        data = {"title": title, "desp": text}
        async with session.post(
                url, data=data,
                timeout=aiohttp.ClientTimeout(total=POST_TIMEOUT)) as r:
            body = await r.text()
            if r.status != 200:
                log.warning("serverchan HTTP %d: %.120s", r.status, body)
                raise RuntimeError(f"serverchan HTTP {r.status}")
            try:
                ok = (await r.json()).get("code") == 0
            except Exception:
                ok = False
            if not ok:
                log.warning("serverchan rejected: %.120s", body)
                raise RuntimeError("serverchan rejected")


class Notifier:
    def __init__(self, channels: List[_Channel], max_queue: int = 64) -> None:
        self._channels = [c for c in channels if c.enabled]
        self.enabled = bool(self._channels)
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue)
        self._done = False
        self._session: Optional[aiohttp.ClientSession] = None

    @classmethod
    def from_env(cls) -> "Notifier":
        return cls([
            TelegramChannel(os.getenv("TELEGRAM_BOT_TOKEN"),
                            os.getenv("TELEGRAM_CHAT_ID")),
            ServerChanChannel(os.getenv("SERVERCHAN_SENDKEY")),
        ])

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
        for channel in self._channels:
            for attempt in (1, 2):
                try:
                    await channel.post(session, text)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning("%s send failed (attempt %d): %r",
                                channel.name, attempt, e)
                if attempt == 1:
                    await asyncio.sleep(RETRY_DELAY_SEC)