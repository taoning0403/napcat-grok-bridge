import asyncio
import ipaddress
import socket
from contextlib import suppress
from typing import Any, Self
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .models import GrokWebhook, NormalizedEvent
from .storage import Store


def validate_grok_url(url: str, allowed_hosts: tuple[str, ...]) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("a public HTTPS webhook URL is required")
    if host not in allowed_hosts:
        raise ValueError("webhook host is not allowed")
    try:
        addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError("webhook URL must resolve only to public addresses")
    except OSError as error:
        raise ValueError("webhook host could not be resolved") from error
    return host


class GrokClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        self.client = httpx.AsyncClient(
            timeout=self.settings.grok_timeout_seconds,
            transport=self.transport,
            follow_redirects=False,
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.client:
            await self.client.aclose()

    async def deliver(self, event: NormalizedEvent, config: GrokWebhook) -> None:
        if not self.client:
            raise RuntimeError("GrokClient is not initialized")
        response = await self.client.post(
            config.url,
            json=event.grok_payload(),
            headers={
                "Authorization": f"Bearer {config.key}",
                "User-Agent": self.settings.bridge_user_agent,
            },
        )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"Grok webhook HTTP {response.status_code}")


class GrokDispatcher:
    def __init__(self, settings: Settings, store: Store, client: GrokClient):
        self.settings = settings
        self.store = store
        self.client = client
        self.wake = asyncio.Event()
        self.stopping = asyncio.Event()
        self.delivery_lock = asyncio.Lock()
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.settings.enable_grok_worker and not self.task:
            self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self.stopping.set()
        self.wake.set()
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    def notify(self) -> None:
        self.wake.set()

    async def deliver_once(self) -> dict[str, Any]:
        async with self.delivery_lock:
            config = self.store.grok_config()
            if not config:
                return {"delivered": False, "reason": "grok is not configured"}
            row = self.store.next_pending_event()
            if not row:
                return {"delivered": False, "reason": "no pending event"}
            event = NormalizedEvent(
                event_id=row["event_id"],
                message_id=row["message_id"],
                self_id=row["self_id"],
                user_id=row["user_id"],
                text=row["text"],
                created_at=row["created_at"],
            )
            try:
                await self.client.deliver(event, config)
                self.store.mark_delivered(event.event_id)
                return {"delivered": True, "event_id": event.event_id}
            except (httpx.HTTPError, RuntimeError, OSError) as error:
                attempts = row["attempts"] + 1
                message = str(error) or type(error).__name__
                self.store.mark_retry(event.event_id, attempts, message)
                return {"delivered": False, "event_id": event.event_id, "error": message[:200], "attempts": attempts}

    async def _run(self) -> None:
        while not self.stopping.is_set():
            await self.deliver_once()
            self.wake.clear()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.wake.wait(), timeout=1)
