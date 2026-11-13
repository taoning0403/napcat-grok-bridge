import base64
import binascii
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

import httpx

from .config import Settings

IMAGE_PATH = re.compile(r"images/\d{4}/\d{2}/[A-Za-z0-9_-]{32}\.(png|jpg|gif|webp)")


class ImageHostError(ValueError):
    pass


class ImageHostUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class HostedImage:
    path: str
    media_type: str
    extension: str
    content: bytes

    @property
    def size(self) -> int:
        return len(self.content)


def detect_image(content: bytes) -> tuple[str, str] | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif", "gif"
    if len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


def decode_image(data: str, maximum_bytes: int) -> tuple[bytes, str, str]:
    if data.startswith("data:"):
        header, separator, data = data.partition(",")
        if not separator or not header.lower().startswith("data:image/") or not header.lower().endswith(";base64"):
            raise ImageHostError("data URL must contain a Base64 image")
    try:
        content = base64.b64decode("".join(data.split()), validate=True)
    except (binascii.Error, ValueError) as error:
        raise ImageHostError("invalid Base64 image") from error
    if not content or len(content) > maximum_bytes:
        raise ImageHostError(f"decoded image must be 1-{maximum_bytes} bytes")
    detected = detect_image(content)
    if not detected:
        raise ImageHostError("only PNG, JPEG, GIF, and WebP images are supported")
    media_type, extension = detected
    return content, media_type, extension


class ImageHost:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport
        self.client: httpx.AsyncClient | None = None
        self.cache_dir = Path(settings.data_dir) / "image-cache"

    async def __aenter__(self) -> Self:
        if self.settings.enable_image_host:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self.client = httpx.AsyncClient(
                base_url="https://api.github.com",
                timeout=self.settings.github_image_timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.settings.github_image_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": self.settings.bridge_user_agent,
                },
            )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.client:
            await self.client.aclose()

    def api_path(self, path: str) -> str:
        return f"/repos/{self.settings.github_image_repository}/contents/{path}"

    def cache_path(self, path: str) -> Path:
        return self.cache_dir / path

    def cache_image(self, path: str, content: bytes) -> None:
        destination = self.cache_path(path)
        temporary = destination.with_name(destination.name + ".tmp")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(content)
            temporary.replace(destination)
        except OSError:
            pass

    def validate_content(self, path: str, content: bytes) -> HostedImage:
        if len(content) > self.settings.image_host_max_bytes:
            raise ImageHostUnavailable("image exceeds the configured size limit")
        detected = detect_image(content)
        if not detected or not path.endswith(f".{detected[1]}"):
            raise ImageHostUnavailable("image content is invalid")
        media_type, extension = detected
        return HostedImage(path, media_type, extension, content)

    async def upload(self, data: str) -> HostedImage:
        if not self.client:
            raise RuntimeError("ImageHost is not initialized")
        content, media_type, extension = decode_image(data, self.settings.image_host_max_bytes)
        month = datetime.now(UTC).strftime("%Y/%m")
        path = f"images/{month}/{secrets.token_urlsafe(24)}.{extension}"
        try:
            response = await self.client.put(
                self.api_path(path),
                json={
                    "message": f"Upload image {path}",
                    "content": base64.b64encode(content).decode(),
                    "branch": self.settings.github_image_branch,
                },
            )
        except httpx.HTTPError as error:
            raise ImageHostUnavailable("GitHub image upload unavailable") from error
        if response.status_code != 201:
            raise ImageHostUnavailable(f"GitHub image upload returned HTTP {response.status_code}")
        self.cache_image(path, content)
        return HostedImage(path, media_type, extension, content)

    async def download(self, path: str) -> HostedImage | None:
        if not self.client:
            raise RuntimeError("ImageHost is not initialized")
        if not IMAGE_PATH.fullmatch(path):
            return None
        try:
            content = self.cache_path(path).read_bytes()
        except OSError:
            content = None
        if content is not None:
            return self.validate_content(path, content)
        try:
            response = await self.client.get(
                self.api_path(path),
                params={"ref": self.settings.github_image_branch},
                headers={"Accept": "application/vnd.github.raw+json"},
            )
        except httpx.HTTPError as error:
            raise ImageHostUnavailable("GitHub image download unavailable") from error
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ImageHostUnavailable(f"GitHub image download returned HTTP {response.status_code}")
        image = self.validate_content(path, response.content)
        self.cache_image(path, image.content)
        return image
