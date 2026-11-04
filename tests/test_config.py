import pytest
from conftest import environment

from napcat_grok_bridge.config import Settings, SettingsError


def test_legacy_environment_aliases(tmp_path):
    env = environment(tmp_path)
    env["POC_TOKEN"] = env.pop("BRIDGE_TOKEN")
    env["ALLOWED_USER_ID"] = env.pop("ALLOWED_PEER_ID")
    settings = Settings.from_env(env)
    assert settings.bridge_token == env["POC_TOKEN"]
    assert settings.allowed_peer_id == int(env["ALLOWED_USER_ID"])


def test_grok_url_and_key_must_be_paired(tmp_path):
    env = environment(tmp_path, GROK_WEBHOOK_URL="https://grok.test/hook")
    with pytest.raises(SettingsError, match="must be set together"):
        Settings.from_env(env)


def test_peer_and_bot_must_differ(tmp_path):
    env = environment(tmp_path)
    env["BOT_USER_ID"] = env["ALLOWED_PEER_ID"]
    with pytest.raises(SettingsError, match="must be different"):
        Settings.from_env(env)


def test_peer_id_does_not_require_legacy_alias(tmp_path):
    env = environment(tmp_path)
    assert Settings.from_env(env).allowed_peer_id == 111111


def test_image_host_configuration(tmp_path):
    env = environment(tmp_path, ENABLE_IMAGE_HOST="true")
    with pytest.raises(SettingsError, match="IMAGE_HOST_PUBLIC_BASE_URL"):
        Settings.from_env(env)

    env["IMAGE_HOST_PUBLIC_BASE_URL"] = "https://napcat-api.example.com"
    with pytest.raises(SettingsError, match="GITHUB_IMAGE_TOKEN"):
        Settings.from_env(env)

    env["GITHUB_IMAGE_TOKEN"] = "github_pat_test_123456"
    with pytest.raises(SettingsError, match="GITHUB_IMAGE_REPOSITORY"):
        Settings.from_env(env)

    env["GITHUB_IMAGE_REPOSITORY"] = "owner/images"
    settings = Settings.from_env(env)
    assert settings.enable_image_host is True
    assert settings.github_image_branch == "main"
