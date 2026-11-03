import os
from typing import Any

import pytest
from fastapi.testclient import TestClient

os.environ.update(
    {
        "ONEBOT_TOKEN": "onebot-token-123456",
        "BRIDGE_TOKEN": "bridge-token-123456",
        "EVENT_TOKEN": "event-token-1234567",
        "ALLOWED_PEER_ID": "111111",
        "BOT_USER_ID": "222222",
        "DATA_DIR": "/tmp/napcat-grok-bridge-tests",
        "ENABLE_GROK_WORKER": "false",
    }
)

from napcat_grok_bridge.config import Settings
from napcat_grok_bridge.main import create_app
from napcat_grok_bridge.models import UpstreamReply

BRIDGE_TOKEN = "bridge-token-123456"
EVENT_TOKEN = "event-token-1234567"
PEER_ID = 111111
BOT_ID = 222222


def environment(tmp_path, **overrides):
    env = {
        "ONEBOT_URL": "http://napcat.test:3000",
        "ONEBOT_TOKEN": "onebot-token-123456",
        "BRIDGE_TOKEN": BRIDGE_TOKEN,
        "EVENT_TOKEN": EVENT_TOKEN,
        "ALLOWED_PEER_ID": str(PEER_ID),
        "BOT_USER_ID": str(BOT_ID),
        "DATA_DIR": str(tmp_path),
        "GROK_ALLOWED_HOSTS": "grok.test",
        "ENABLE_GROK_WORKER": "false",
        "BRIDGE_USER_AGENT": "GrokBot-NapCat-Bridge/1.0",
    }
    env.update(overrides)
    return env


@pytest.fixture
def settings(tmp_path):
    return Settings.from_env(environment(tmp_path))


class FakeOneBot:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.next_message_id = 1001

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, path: str, payload: dict[str, Any]):
        self.calls.append((path, payload))
        if path == "/get_login_info":
            return UpstreamReply(200, {"status": "ok", "retcode": 0, "data": {"user_id": BOT_ID}})
        if path == "/send_private_msg":
            message_id = self.next_message_id
            self.next_message_id += 1
            return UpstreamReply(200, {"status": "ok", "retcode": 0, "data": {"message_id": message_id}})
        return UpstreamReply(404, {"status": "failed", "retcode": 1404})


@pytest.fixture
def bridge(settings):
    onebot = FakeOneBot()
    app = create_app(settings, onebot=onebot)
    with TestClient(app) as client:
        yield client, app, onebot, settings


def auth_headers(token=BRIDGE_TOKEN):
    return {"Authorization": f"Bearer {token}"}


def private_event(**overrides):
    event = {
        "post_type": "message",
        "message_type": "private",
        "self_id": BOT_ID,
        "user_id": PEER_ID,
        "message_id": 5001,
        "message": [{"type": "text", "data": {"text": "hello"}}],
    }
    event.update(overrides)
    return event
