import os
from importlib.resources import files


def main() -> None:
    template = files("napcat_grok_bridge").joinpath("templates/grok-routine.zh-CN.md").read_text()
    replacements = {
        "{{ALLOWED_PEER_ID}}": os.environ.get("ALLOWED_PEER_ID", os.environ.get("ALLOWED_USER_ID", "<私聊对象 QQ>")),
        "{{BOT_USER_ID}}": os.environ.get("BOT_USER_ID", "<机器人 QQ>"),
        "{{PUBLIC_BASE_URL}}": os.environ.get("PUBLIC_BASE_URL", "https://napcat-api.example.com").rstrip("/"),
        "{{BRIDGE_USER_AGENT}}": os.environ.get("BRIDGE_USER_AGENT", "GrokBot-NapCat-Bridge/1.0"),
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    print(template)


if __name__ == "__main__":
    main()
