from conftest import BOT_ID, EVENT_TOKEN, PEER_ID, auth_headers, private_event


def test_health_and_status(bridge):
    client, _, _, settings = bridge
    assert client.get("/health").status_code == 200
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


def test_message_array_normalization(bridge):
    client, _, _, _ = bridge
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
