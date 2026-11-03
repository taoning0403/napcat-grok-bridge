import os
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
        return cls(
            onebot_url=onebot_url,
            onebot_token=_secret(env, "ONEBOT_TOKEN"),
            bridge_token=bridge_token,
            event_token=event_token,
            allowed_peer_id=allowed_peer_id,
            bot_user_id=bot_user_id,
            data_dir=env.get("DATA_DIR", "/data"),
            public_base_url=env.get("PUBLIC_BASE_URL", "").rstrip("/"),
            bridge_user_agent=env.get("BRIDGE_USER_AGENT", "GrokBot-NapCat-Bridge/1.0"),
            grok_allowed_hosts=allowed_hosts,
            grok_webhook_url=grok_url,
            grok_webhook_key=grok_key,
            grok_timeout_seconds=float(env.get("GROK_TIMEOUT_SECONDS", "30")),
            onebot_timeout_seconds=float(env.get("ONEBOT_TIMEOUT_SECONDS", "15")),
            enable_grok_worker=_boolean(env.get("ENABLE_GROK_WORKER", "true")),
            enable_api_docs=_boolean(env.get("ENABLE_API_DOCS", "false")),
            max_message_length=_integer(env, "MAX_MESSAGE_LENGTH", 1000),
            retention_days=_integer(env, "RETENTION_DAYS", 7),
        )
