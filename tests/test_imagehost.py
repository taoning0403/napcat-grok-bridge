import base64
import json
import socket

import httpx
from conftest import PEER_ID, FakeOneBot, auth_headers, environment
from fastapi.testclient import TestClient

from napcat_grok_bridge.config import Settings
from napcat_grok_bridge.imagehost import ImageHost
from napcat_grok_bridge.main import create_app

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def public_dns(*_, **__):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]


def image_settings(tmp_path, **overrides):
    return Settings.from_env(
        environment(
            tmp_path,
            ENABLE_IMAGE_HOST="true",
            IMAGE_HOST_PUBLIC_BASE_URL="https://napcat-api.example.com",
            GITHUB_IMAGE_TOKEN="github_pat_test_123456",
            GITHUB_IMAGE_REPOSITORY="taoning0403/napcat-grok-images",
            **overrides,
        )
    )


def test_base64_upload_proxy_and_qq_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "PUT":
            payload = json.loads(request.content)
            assert payload["branch"] == "main"
            assert base64.b64decode(payload["content"]) == PNG
            return httpx.Response(201, json={"content": {"path": "image"}})
        assert request.url.params["ref"] == "main"
        return httpx.Response(200, content=PNG)

    settings = image_settings(tmp_path)
    onebot = FakeOneBot()
    image_host = ImageHost(settings, transport=httpx.MockTransport(handler))
    app = create_app(settings, onebot=onebot, image_host=image_host)
    with TestClient(app) as client:
        assert client.post("/grok/images", json={"data": "AAAA"}).status_code == 403
        response = client.post(
            "/grok/images",
            json={"data": "data:image/png;base64," + base64.b64encode(PNG).decode()},
            headers=auth_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["media_type"] == "image/png"
        assert body["size"] == len(PNG)
        assert body["url"].startswith("https://napcat-api.example.com/images/")

        download = client.get("/images/" + body["path"])
        assert download.status_code == 200
        assert download.content == PNG
        assert download.headers["Content-Type"] == "image/png"
        assert "immutable" in download.headers["Cache-Control"]

        sent = client.post(
            "/send_private_msg",
            json={
                "user_id": PEER_ID,
                "message": [{"type": "image", "data": {"file": body["url"]}}],
            },
            headers=auth_headers(),
        )
        assert sent.status_code == 200

    restarted_host = ImageHost(settings, transport=httpx.MockTransport(handler))
    restarted_app = create_app(settings, onebot=FakeOneBot(), image_host=restarted_host)
    with TestClient(restarted_app) as client:
        cached = client.get("/images/" + body["path"])
        assert cached.status_code == 200
        assert cached.content == PNG

    assert len(requests) == 1
    assert all(request.headers["Authorization"] == "Bearer github_pat_test_123456" for request in requests)
    assert onebot.calls[-1][1]["message"][0]["data"]["file"] == body["url"]


def test_download_falls_back_to_github_when_cache_is_missing(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=PNG)

    settings = image_settings(tmp_path)
    image_host = ImageHost(settings, transport=httpx.MockTransport(handler))
    app = create_app(settings, onebot=FakeOneBot(), image_host=image_host)
    with TestClient(app) as client:
        response = client.get("/images/images/2026/11/" + "b" * 32 + ".png")
        assert response.status_code == 200
        assert response.content == PNG
    assert len(requests) == 1


def test_invalid_images_do_not_call_github(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(500)

    settings = image_settings(tmp_path)
    image_host = ImageHost(settings, transport=httpx.MockTransport(handler))
    app = create_app(settings, onebot=FakeOneBot(), image_host=image_host)
    with TestClient(app) as client:
        for data in (
            "not base64",
            base64.b64encode(b"not an image").decode(),
            "data:text/plain;base64," + base64.b64encode(PNG).decode(),
        ):
            response = client.post("/grok/images", json={"data": data}, headers=auth_headers())
            assert response.status_code == 400
    assert requests == []


def test_missing_image_and_github_failure(tmp_path):
    def handler(request):
        if request.method == "PUT":
            return httpx.Response(403, json={"message": "forbidden"})
        return httpx.Response(404, json={"message": "not found"})

    settings = image_settings(tmp_path)
    image_host = ImageHost(settings, transport=httpx.MockTransport(handler))
    app = create_app(settings, onebot=FakeOneBot(), image_host=image_host)
    with TestClient(app) as client:
        upload = client.post(
            "/grok/images",
            json={"data": base64.b64encode(PNG).decode()},
            headers=auth_headers(),
        )
        assert upload.status_code == 502
        assert "403" in upload.json()["message"]
        missing = client.get("/images/images/2026/11/" + "a" * 32 + ".png")
        assert missing.status_code == 404


def test_image_host_is_disabled_by_default(bridge):
    client, _, _, _ = bridge
    response = client.post("/grok/images", json={"data": "AAAA"}, headers=auth_headers())
    assert response.status_code == 404
