import asyncio
import json
import socket
import stat

import httpx
import pytest
from conftest import EVENT_TOKEN, auth_headers, environment, private_event
from fastapi.testclient import TestClient

from napcat_grok_bridge.config import Settings
from napcat_grok_bridge.grok import GrokClient, validate_grok_url
from napcat_grok_bridge.main import create_app


def public_dns(*_, **__):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]


def configured_bridge(settings, transport):
    app = create_app(settings, grok_client=GrokClient(settings, transport=transport))
    return app


def test_grok_configuration_and_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"accepted": True})

    settings = Settings.from_env(environment(tmp_path))
    app = configured_bridge(settings, httpx.MockTransport(handler))
    with TestClient(app):
        invalid = TestClient(app)
        response = invalid.post(
            "/grok/config",
            json={"url": "https://not-allowed.test/hook", "key": "webhook-key-123456"},
            headers=auth_headers(),
        )
        assert response.status_code == 400

        response = invalid.post(
            "/grok/config",
            json={"url": "https://grok.test/hook", "key": "webhook-key-123456"},
            headers=auth_headers(),
        )
        assert response.status_code == 200
        assert response.json()["grok_host"] == "grok.test"

        response = invalid.post(f"/napcat/event?access_token={EVENT_TOKEN}", json=private_event())
        assert response.json()["accepted"] is True
        result = asyncio.run(app.state.dispatcher.deliver_once())
        assert result["delivered"] is True

        assert len(requests) == 1
        request = requests[0]
        payload = json.loads(request.content)
        assert payload["type"] == "qq_private_message"
        assert payload["schema_version"] == 2
        assert payload["text"] == "hello"
        assert payload["images"] == []
        assert request.headers["User-Agent"] == "GrokBot-NapCat-Bridge/1.0"
        assert request.headers["Authorization"] == "Bearer webhook-key-123456"
        assert stat.S_IMODE(app.state.store.grok_config_path.stat().st_mode) == 0o600
        assert asyncio.run(app.state.dispatcher.deliver_once())["reason"] == "no pending event"


def test_grok_image_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"accepted": True})

    settings = Settings.from_env(environment(tmp_path))
    app = configured_bridge(settings, httpx.MockTransport(handler))
    with TestClient(app):
        client = TestClient(app)
        client.post(
            "/grok/config",
            json={"url": "https://grok.test/hook", "key": "webhook-key-123456"},
            headers=auth_headers(),
        )
        event = private_event(
            message_id=5100,
            message=[
                {"type": "text", "data": {"text": "看这张图"}},
                {"type": "image", "data": {"url": "https://multimedia.example/photo.png"}},
                {"type": "image", "data": {"url": "http://unsafe.example/photo.png"}},
            ],
        )
        response = client.post(f"/napcat/event?access_token={EVENT_TOKEN}", json=event)
        assert response.json()["accepted"] is True
        assert asyncio.run(app.state.dispatcher.deliver_once())["delivered"] is True

    payload = json.loads(requests[0].content)
    assert payload["schema_version"] == 2
    assert payload["text"] == "看这张图[图片][图片]"
    assert payload["images"] == [{"url": "https://multimedia.example/photo.png"}]


def test_grok_retry_then_success(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    attempts = []

    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(200, json={"accepted": True})

    settings = Settings.from_env(environment(tmp_path))
    app = configured_bridge(settings, httpx.MockTransport(handler))
    with TestClient(app):
        client = TestClient(app)
        client.post(
            "/grok/config",
            json={"url": "https://grok.test/hook", "key": "webhook-key-123456"},
            headers=auth_headers(),
        )
        client.post(f"/napcat/event?access_token={EVENT_TOKEN}", json=private_event())
        first = asyncio.run(app.state.dispatcher.deliver_once())
        assert first["delivered"] is False
        assert first["attempts"] == 1
        assert first["error"] == "Grok webhook HTTP 503"

        with app.state.store.connection() as connection:
            connection.execute("UPDATE events SET next_attempt = 0")
        second = asyncio.run(app.state.dispatcher.deliver_once())
        assert second["delivered"] is True
        assert len(attempts) == 2


def test_private_grok_address_is_rejected(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_, **__: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError, match="public addresses"):
        validate_grok_url("https://grok.test/hook", ("grok.test",))
