import json
import logging
import socket
import threading
import time

import uvicorn
from conftest import EVENT_TOKEN, private_event


def test_raw_chunked_event_and_safe_access_log(bridge, caplog):
    _, app, _, _ = bridge
    caplog.set_level(logging.INFO, logger="napcat_grok_bridge")
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started
        port = server.servers[0].sockets[0].getsockname()[1]
        event = json.dumps(private_event(message_id=7001)).encode()
        split = len(event) // 2
        request = (
            f"POST /napcat/event?access_token={EVENT_TOKEN} HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Content-Type: application/json\r\n"
            "Transfer-Encoding: chunked\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        for chunk in (event[:split], event[split:]):
            request += f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n"
        request += b"0\r\n\r\n"
        with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
            connection.sendall(request)
            response = b""
            while True:
                part = connection.recv(4096)
                if not part:
                    break
                response += part
        assert response.split(b" ", 2)[1] == b"200"
        body = json.loads(response.split(b"\r\n\r\n", 1)[1])
        assert body["accepted"] is True
        assert EVENT_TOKEN not in caplog.text
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert not thread.is_alive()
