from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class GrokWebhook:
    url: str
    key: str


@dataclass(frozen=True)
class NormalizedEvent:
    event_id: str
    message_id: str
    self_id: str
    user_id: int
    text: str
    created_at: float

    def grok_payload(self) -> dict[str, Any]:
        return {
            "type": "qq_private_message",
            "schema_version": 1,
            "event_id": self.event_id,
            "message_id": self.message_id,
            "self_id": self.self_id,
            "user_id": self.user_id,
            "text": self.text,
            "timestamp": self.created_at,
        }


@dataclass(frozen=True)
class UpstreamReply:
    status: int
    payload: Any


class GrokConfigRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    key: str = Field(min_length=16, max_length=4096)


class PrivateMessageRequest(BaseModel):
    user_id: int
    message: str = Field(min_length=1, max_length=1000)
