import time
from typing import Any, Self
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .models import NormalizedEvent, UpstreamReply
from .storage import Store

MAX_IMAGES = 4


class OneBotUnavailable(RuntimeError):
    pass


class OneBotClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        self.client = httpx.AsyncClient(
            base_url=self.settings.onebot_url,
            timeout=self.settings.onebot_timeout_seconds,
            transport=self.transport,
            headers={
                "Authorization": f"Bearer {self.settings.onebot_token}",
                "User-Agent": self.settings.bridge_user_agent,
            },
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.client:
            await self.client.aclose()

    async def post(self, path: str, payload: dict[str, Any]) -> UpstreamReply:
        if not self.client:
            raise RuntimeError("OneBotClient is not initialized")
        try:
            response = await self.client.post(path, json=payload)
        except httpx.HTTPError as error:
            raise OneBotUnavailable("NapCat upstream unavailable") from error
        try:
            return UpstreamReply(response.status_code, response.json())
        except ValueError:
            return UpstreamReply(response.status_code, {"status": "failed", "message": "invalid NapCat response"})


def incoming_image_url(data: dict[str, Any]) -> str | None:
    url = data.get("url")
    if not isinstance(url, str) or len(url) > 4096:
        return None
    url = url.strip()
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password
    except ValueError:
        return None
    return url if valid else None


def extract_message(payload: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    message = payload.get("message")
    images: list[str] = []
    if isinstance(message, str):
        text = message
    elif isinstance(message, list):
        parts = []
        for segment in message:
            if not isinstance(segment, dict):
                continue
            kind = segment.get("type", "unknown")
            data = segment.get("data") or {}
            if not isinstance(data, dict):
                data = {}
            if kind == "text":
                parts.append(str(data.get("text", "")))
            elif kind == "image":
                parts.append("[图片]")
                url = incoming_image_url(data)
                if url and len(images) < MAX_IMAGES:
                    images.append(url)
            elif kind == "face":
                parts.append("[表情]")
            elif kind == "reply":
                parts.append("[回复消息]")
            elif kind == "at":
                parts.append(f"@{data.get('qq', '')}")
            else:
                parts.append(f"[{kind}]")
        text = "".join(parts)
    else:
        text = str(payload.get("raw_message") or "")
    return (text.strip() or "[非文本消息]")[:6000], tuple(images)


def extract_text(payload: dict[str, Any]) -> str:
    return extract_message(payload)[0]


class EventProcessor:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store

    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("post_type") not in {"message", "message_sent"}:
            return {"accepted": False, "reason": "not a message"}
        if payload.get("message_type") != "private":
            return {"accepted": False, "reason": "not private"}
        try:
            user_id = int(payload.get("user_id"))
            self_id = int(payload.get("self_id"))
        except (TypeError, ValueError):
            return {"accepted": False, "reason": "invalid user_id or self_id"}
        if user_id != self.settings.allowed_peer_id:
            return {"accepted": False, "reason": "user_id is not allowed"}
        if self_id != self.settings.bot_user_id:
            return {"accepted": False, "reason": "self_id is not allowed"}
        message_id = payload.get("message_id")
        text, images = extract_message(payload)
        if self.store.is_outbound(message_id):
            return {"accepted": False, "reason": "bridge outbound message"}
        if payload.get("post_type") == "message_sent" and self.store.consume_pending(user_id, text):
            self.store.record_outbound(message_id)
            return {"accepted": False, "reason": "pending bridge outbound message"}
        event_id = f"qq:{self_id}:{message_id}" if message_id is not None else f"qq:{self_id}:{time.time_ns()}"
        event = NormalizedEvent(
            event_id=event_id,
            message_id=str(message_id),
            self_id=str(self_id),
            user_id=user_id,
            text=text,
            created_at=time.time(),
            images=images,
        )
        accepted, reason = self.store.insert_event(event)
        return {"accepted": accepted, "event_id": event_id, "reason": reason}
