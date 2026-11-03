import argparse
import json
import os
from typing import Any

import httpx

from .config import Settings


def print_check(name: str, ok: bool, details: Any = None) -> None:
    print(json.dumps({"check": name, "ok": ok, "details": details}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Check a running NapCat Grok Bridge")
    parser.add_argument("--base-url", default=os.environ.get("DOCTOR_BASE_URL", "http://napcat-grok-bridge:18080"))
    parser.add_argument("--require-grok", action="store_true")
    parser.add_argument("--send-test", action="store_true", help="Send one real test private message")
    args = parser.parse_args()
    settings = Settings.from_env()
    base_url = args.base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {settings.bridge_token}",
        "User-Agent": settings.bridge_user_agent,
    }
    failures = 0
    with httpx.Client(base_url=base_url, headers=headers, timeout=20, follow_redirects=False) as client:
        try:
            response = client.get("/health")
            ok = response.is_success and response.json().get("status") == "ok"
            print_check("bridge_health", ok, response.json())
            failures += not ok
        except (httpx.HTTPError, ValueError) as error:
            print_check("bridge_health", False, str(error))
            raise SystemExit(1)

        response = client.post("/get_login_info", json={})
        body = response.json()
        ok = response.is_success and body.get("retcode") == 0
        details = {
            "http": response.status_code,
            "retcode": body.get("retcode"),
            "bot_user_id": body.get("data", {}).get("user_id"),
        }
        print_check("napcat_login", ok, details)
        failures += not ok

        response = client.get("/bridge/status")
        body = response.json()
        ok = response.is_success and body.get("status") == "ok"
        print_check("bridge_status", ok, body)
        failures += not ok
        if args.require_grok and not body.get("grok_configured"):
            print_check("grok_configured", False, "run the Grok Routine setup first")
            failures += 1

        if args.send_test:
            response = client.post(
                "/send_private_msg",
                json={
                    "user_id": settings.allowed_peer_id,
                    "message": "【NapCat Grok Bridge】部署验证，无需回复。",
                },
            )
            body = response.json()
            ok = response.is_success and body.get("retcode") == 0
            print_check(
                "send_private_msg",
                ok,
                {
                    "http": response.status_code,
                    "retcode": body.get("retcode"),
                    "message_id": body.get("data", {}).get("message_id"),
                },
            )
            failures += not ok

    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
