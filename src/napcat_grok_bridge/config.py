import os
import re
from collections.abc import Mapping
from dataclasses import dataclass


class SettingsError(ValueError):
    pass


def _boolean(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _integer(env: Mapping[str, str], name: str, default: int | None = None) -> int:
    value = env.get(name)
    if value is None and default is not None:
        return default
    try:
        return int(value or "")
    except ValueError as error:
        raise SettingsError(f"{name} must be an integer") from error


def _secret(env: Mapping[str, str], name: str, fallback: str | None = None) -> str:
    value = env.get(name) or (env.get(fallback) if fallback else None) or ""
    if len(value) < 16:
        raise SettingsError(f"{name} must contain at least 16 characters")
    return value


@dataclass(frozen=True)
class Settings:
    onebot_url: str
    onebot_token: str
    bridge_token: str
    event_token: str
    allowed_peer_id: int
    bot_user_id: int
    data_dir: str
    public_base_url: str
    bridge_user_agent: str
    grok_allowed_hosts: tuple[str, ...]
    grok_webhook_url: str
    grok_webhook_key: str
    grok_timeout_seconds: float
    onebot_timeout_seconds: float
    enable_grok_worker: bool
    enable_api_docs: bool
    max_message_length: int
    retention_days: int
    enable_image_host: bool
    image_host_public_base_url: str
    image_host_max_bytes: int
    github_image_token: str
    github_image_repository: str
    github_image_branch: str
    github_image_timeout_seconds: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        onebot_url = env.get("ONEBOT_URL", "http://napcat:3000").rstrip("/")
        if not onebot_url.startswith(("http://", "https://")):
            raise SettingsError("ONEBOT_URL must use HTTP or HTTPS")
        bridge_token = _secret(env, "BRIDGE_TOKEN", "POC_TOKEN")
        event_token = _secret(env, "EVENT_TOKEN")
        if bridge_token == event_token:
            raise SettingsError("BRIDGE_TOKEN and EVENT_TOKEN must be different")
        peer_value = env.get("ALLOWED_PEER_ID") or env.get("ALLOWED_USER_ID")
        if not peer_value:
            raise SettingsError("ALLOWED_PEER_ID is required")
        try:
            allowed_peer_id = int(peer_value)
        except ValueError as error:
            raise SettingsError("ALLOWED_PEER_ID must be an integer") from error
        bot_user_id = _integer(env, "BOT_USER_ID")
        if allowed_peer_id == bot_user_id:
            raise SettingsError("ALLOWED_PEER_ID and BOT_USER_ID must be different")
        grok_url = env.get("GROK_WEBHOOK_URL", "")
        grok_key = env.get("GROK_WEBHOOK_KEY", "")
        if bool(grok_url) != bool(grok_key):
            raise SettingsError("GROK_WEBHOOK_URL and GROK_WEBHOOK_KEY must be set together")
        allowed_hosts = tuple(
            host.strip().lower()
            for host in env.get("GROK_ALLOWED_HOSTS", "api2.cursor.sh").split(",")
            if host.strip()
        )
        if not allowed_hosts:
            raise SettingsError("GROK_ALLOWED_HOSTS cannot be empty")
        public_base_url = env.get("PUBLIC_BASE_URL", "").rstrip("/")
        enable_image_host = _boolean(env.get("ENABLE_IMAGE_HOST", "false"))
        image_host_public_base_url = (env.get("IMAGE_HOST_PUBLIC_BASE_URL") or public_base_url).rstrip("/")
        image_host_max_bytes = _integer(env, "IMAGE_HOST_MAX_BYTES", 10 * 1024 * 1024)
        github_image_token = env.get("GITHUB_IMAGE_TOKEN", "")
        github_image_repository = env.get("GITHUB_IMAGE_REPOSITORY", "")
        github_image_branch = env.get("GITHUB_IMAGE_BRANCH", "main")
        if enable_image_host:
            if not image_host_public_base_url.startswith("https://"):
                raise SettingsError("IMAGE_HOST_PUBLIC_BASE_URL must use HTTPS when the image host is enabled")
            if not 1024 <= image_host_max_bytes <= 50 * 1024 * 1024:
                raise SettingsError("IMAGE_HOST_MAX_BYTES must be between 1024 and 52428800")
            if len(github_image_token) < 16:
                raise SettingsError("GITHUB_IMAGE_TOKEN must contain at least 16 characters")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", github_image_repository):
                raise SettingsError("GITHUB_IMAGE_REPOSITORY must use owner/repository format")
            if not github_image_branch:
                raise SettingsError("GITHUB_IMAGE_BRANCH cannot be empty")
        return cls(
            onebot_url=onebot_url,
            onebot_token=_secret(env, "ONEBOT_TOKEN"),
            bridge_token=bridge_token,
            event_token=event_token,
            allowed_peer_id=allowed_peer_id,
            bot_user_id=bot_user_id,
            data_dir=env.get("DATA_DIR", "/data"),
            public_base_url=public_base_url,
            bridge_user_agent=env.get("BRIDGE_USER_AGENT", "GrokBot-NapCat-Bridge/1.0"),
            grok_allowed_hosts=allowed_hosts,
            grok_webhook_url=grok_url,
            grok_webhook_key=grok_key,
            grok_timeout_seconds=float(env.get("GROK_TIMEOUT_SECONDS", "30")),
            onebot_timeout_seconds=float(env.get("ONEBOT_TIMEOUT_SECONDS", "120")),
            enable_grok_worker=_boolean(env.get("ENABLE_GROK_WORKER", "true")),
            enable_api_docs=_boolean(env.get("ENABLE_API_DOCS", "false")),
            max_message_length=_integer(env, "MAX_MESSAGE_LENGTH", 1000),
            retention_days=_integer(env, "RETENTION_DAYS", 7),
            enable_image_host=enable_image_host,
            image_host_public_base_url=image_host_public_base_url,
            image_host_max_bytes=image_host_max_bytes,
            github_image_token=github_image_token,
            github_image_repository=github_image_repository,
            github_image_branch=github_image_branch,
            github_image_timeout_seconds=float(env.get("GITHUB_IMAGE_TIMEOUT_SECONDS", "30")),
        )
