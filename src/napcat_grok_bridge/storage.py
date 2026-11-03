import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import GrokWebhook, NormalizedEvent


class Store:
    def __init__(self, data_dir: str, retention_days: int = 7, initial_grok_config: GrokWebhook | None = None):
        self.data_dir = Path(data_dir)
        self.database_path = self.data_dir / "bridge.db"
        self.grok_config_path = self.data_dir / "grok_webhook.json"
        self.retention_days = retention_days
        self.initial_grok_config = initial_grok_config
        self.pending_outbound: list[tuple[float, int, str]] = []
        self.pending_lock = threading.Lock()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - self.retention_days * 86400
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    message_id TEXT,
                    self_id TEXT,
                    user_id INTEGER,
                    text TEXT,
                    created_at REAL,
                    attempts INTEGER DEFAULT 0,
                    delivered INTEGER DEFAULT 0,
                    next_attempt REAL DEFAULT 0,
                    delivered_at REAL,
                    last_error TEXT
                )"""
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS outbound (message_id TEXT PRIMARY KEY, created_at REAL)"
            )
            connection.execute("DELETE FROM outbound WHERE created_at < ?", (cutoff,))
            connection.execute("DELETE FROM events WHERE delivered = 1 AND delivered_at < ?", (cutoff,))

    def insert_event(self, event: NormalizedEvent) -> tuple[bool, str]:
        with self.connection() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO events(event_id, message_id, self_id, user_id, text, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    event.message_id,
                    event.self_id,
                    event.user_id,
                    event.text,
                    event.created_at,
                ),
            )
        return cursor.rowcount == 1, "stored" if cursor.rowcount == 1 else "duplicate"

    def next_pending_event(self) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(
                "SELECT * FROM events WHERE delivered = 0 AND next_attempt <= ? ORDER BY created_at LIMIT 1",
                (time.time(),),
            ).fetchone()

    def mark_delivered(self, event_id: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE events SET delivered = 1, delivered_at = ?, last_error = NULL WHERE event_id = ?",
                (time.time(), event_id),
            )

    def mark_retry(self, event_id: str, attempts: int, message: str) -> None:
        delay = min(300, 5 * (2 ** min(attempts - 1, 6)))
        with self.connection() as connection:
            connection.execute(
                "UPDATE events SET attempts = ?, next_attempt = ?, last_error = ? WHERE event_id = ?",
                (attempts, time.time() + delay, message[:200], event_id),
            )

    def record_outbound(self, message_id: Any) -> None:
        if message_id is None:
            return
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO outbound(message_id, created_at) VALUES (?, ?)",
                (str(message_id), time.time()),
            )

    def is_outbound(self, message_id: Any) -> bool:
        if message_id is None:
            return False
        with self.connection() as connection:
            row = connection.execute("SELECT 1 FROM outbound WHERE message_id = ?", (str(message_id),)).fetchone()
        return row is not None

    def mark_pending(self, peer_id: int, text: str) -> None:
        with self.pending_lock:
            self.pending_outbound.append((time.time(), peer_id, text))

    def discard_pending(self, peer_id: int, text: str) -> None:
        with self.pending_lock:
            for index, (_, pending_peer, pending_text) in enumerate(self.pending_outbound):
                if pending_peer == peer_id and pending_text == text:
                    self.pending_outbound.pop(index)
                    return

    def consume_pending(self, peer_id: int, text: str) -> bool:
        now = time.time()
        with self.pending_lock:
            self.pending_outbound[:] = [item for item in self.pending_outbound if now - item[0] < 60]
            for index, (_, pending_peer, pending_text) in enumerate(self.pending_outbound):
                if pending_peer == peer_id and pending_text == text:
                    self.pending_outbound.pop(index)
                    return True
        return False

    def grok_config(self) -> GrokWebhook | None:
        try:
            with self.grok_config_path.open() as config_file:
                data = json.load(config_file)
            return GrokWebhook(url=data["url"], key=data["key"])
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
            return self.initial_grok_config

    def save_grok_config(self, config: GrokWebhook) -> None:
        temporary_path = self.grok_config_path.with_suffix(".tmp")
        with temporary_path.open("w") as config_file:
            json.dump({"url": config.url, "key": config.key}, config_file)
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, self.grok_config_path)

    def status(self) -> dict[str, Any]:
        with self.connection() as connection:
            pending = connection.execute("SELECT COUNT(*) FROM events WHERE delivered = 0").fetchone()[0]
            delivered = connection.execute("SELECT COUNT(*) FROM events WHERE delivered = 1").fetchone()[0]
            latest = connection.execute(
                "SELECT event_id, attempts, last_error, next_attempt FROM events "
                "WHERE delivered = 0 ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        result: dict[str, Any] = {"pending_events": pending, "delivered_events": delivered}
        if latest:
            result["latest_pending"] = dict(latest)
        return result
