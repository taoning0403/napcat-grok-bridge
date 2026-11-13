from napcat_grok_bridge.routine import main


def test_routine_rendering(monkeypatch, capsys):
    monkeypatch.setenv("ALLOWED_PEER_ID", "111111")
    monkeypatch.setenv("BOT_USER_ID", "222222")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://napcat-api.example.com/")
    monkeypatch.setenv("BRIDGE_USER_AGENT", "GrokBot-NapCat-Bridge/1.0")
    main()
    output = capsys.readouterr().out
    assert "user_id=111111" in output
    assert "222222 是 NapCat 机器人登录账号" in output
    assert "https://napcat-api.example.com/send_private_msg" in output
    assert "schema_version=2" in output
    assert '"type":"image"' in output
    assert '"file":"https://example.com/image.png"' in output
    assert "https://napcat-api.example.com/grok/images" in output
    assert "纯 Base64 或 data:image/png;base64" in output
    assert "502、超时或结果不明确" in output
    assert "不得自动重试" in output
    assert "{{" not in output
    assert "BRIDGE_TOKEN" in output
