import json
import socket

from conftest import BOT_ID, EVENT_TOKEN, PEER_ID, auth_headers, private_event


def public_dns(*_, **__):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]


def test_health_and_status(bridge):
    client, _, _, settings = bridge
    assert client.get("/health").status_code == 200
    assert client.get("/health").json()["version"] == "0.3.0"
    assert client.get("/bridge/status").status_code == 403
    response = client.get("/bridge/status", headers=auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["allowed_peer_id"] == settings.allowed_peer_id
    assert body["bot_user_id"] == settings.bot_user_id
    assert body["grok_configured"] is False


def test_event_filtering_and_deduplication(bridge):
    client, _, _, _ = bridge
    url = f"/napcat/event?access_token={EVENT_TOKEN}"
    assert client.post("/napcat/event", json=private_event()).status_code == 403

    response = client.post(url, json=private_event())
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["event_id"].startswith("qq:")

    duplicate = client.post(url, json=private_event()).json()
    assert duplicate["accepted"] is False
    assert duplicate["reason"] == "duplicate"

    for event in (
        private_event(message_id=5002, user_id=999999),
        private_event(message_id=5003, message_type="group"),
        private_event(message_id=5004, self_id=999999),
        private_event(message_id=5005, user_id=BOT_ID),
    ):
        assert client.post(url, json=event).json()["accepted"] is False

    status = client.get("/bridge/status", headers=auth_headers()).json()
    assert status["pending_events"] == 1


def test_private_message_whitelist_and_outbound_filtering(bridge):
    client, _, onebot, _ = bridge
    denied = client.post("/send_private_msg", json={"user_id": 999999, "message": "no"}, headers=auth_headers())
    assert denied.status_code == 403

    response = client.post(
        "/send_private_msg",
        json={"user_id": PEER_ID, "message": "bot reply"},
        headers=auth_headers(),
    )
    assert response.status_code == 200
    message_id = response.json()["data"]["message_id"]
    assert onebot.calls[-1][1]["user_id"] == PEER_ID
    assert onebot.calls[-1][1]["auto_escape"] is True

    url = f"/napcat/event?access_token={EVENT_TOKEN}"
    sent = private_event(
        post_type="message_sent",
        message_id=message_id,
        message="bot reply",
    )
    assert client.post(url, json=sent).json()["accepted"] is False

    race = private_event(post_type="message_sent", message_id=6001, message="bot reply")
    result = client.post(url, json=race).json()
    assert result["accepted"] is False
    assert result["reason"] == "pending bridge outbound message"

    incoming = client.post(url, json=private_event(message_id=6002, message="from phone"))
    assert incoming.json()["accepted"] is True


def test_private_message_with_image(bridge, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    client, _, onebot, _ = bridge
    response = client.post(
        "/send_private_msg",
        json={
            "user_id": PEER_ID,
            "message": [
                {"type": "text", "data": {"text": "请看"}},
                {"type": "image", "data": {"file": "https://images.example/photo.png"}},
            ],
        },
        headers=auth_headers(),
    )
    assert response.status_code == 200
    assert onebot.calls[-1] == (
        "/send_private_msg",
        {
            "user_id": PEER_ID,
            "message": [
                {"type": "text", "data": {"text": "请看"}},
                {"type": "image", "data": {"file": "https://images.example/photo.png"}},
            ],
            "auto_escape": False,
        },
    )

    race = private_event(
        post_type="message_sent",
        message_id=6003,
        message=[
            {"type": "text", "data": {"text": "请看"}},
            {"type": "image", "data": {"url": "https://multimedia.example/photo.png"}},
        ],
    )
    result = client.post(f"/napcat/event?access_token={EVENT_TOKEN}", json=race).json()
    assert result["accepted"] is False
    assert result["reason"] == "pending bridge outbound message"


def test_private_message_rejects_unsafe_images(bridge, monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_, **__: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    client, _, onebot, _ = bridge
    messages = [
        [{"type": "image", "data": {"file": "http://images.example/photo.png"}}],
        [{"type": "image", "data": {"file": "https://internal.test/photo.png"}}],
        [{"type": "image", "data": {"file": "https://user:secret@images.example/photo.png"}}],
        [{"type": "image", "data": {"file": "/tmp/photo.png"}}],
        [{"type": "face", "data": {"id": "1"}}],
    ]
    for message in messages:
        response = client.post(
            "/send_private_msg",
            json={"user_id": PEER_ID, "message": message},
            headers=auth_headers(),
        )
        assert response.status_code == 400
    assert onebot.calls == []


def test_private_message_limits_images(bridge, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    client, _, onebot, _ = bridge
    message = [
        {"type": "image", "data": {"file": f"https://images.example/{index}.png"}}
        for index in range(5)
    ]
    response = client.post(
        "/send_private_msg",
        json={"user_id": PEER_ID, "message": message},
        headers=auth_headers(),
    )
    assert response.status_code == 400
    assert "4 images" in response.json()["message"]
    assert onebot.calls == []


def test_message_array_normalization(bridge):
    client, app, _, _ = bridge
    response = client.post(
        f"/napcat/event?access_token={EVENT_TOKEN}",
        json=private_event(
            message=[
                {"type": "text", "data": {"text": "hello "}},
                {"type": "image", "data": {"url": "https://example.invalid/image"}},
                {"type": "face", "data": {"id": "1"}},
            ]
        ),
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    row = app.state.store.next_pending_event()
    assert row["text"] == "hello [图片][表情]"
    assert json.loads(row["images"]) == ["https://example.invalid/image"]
