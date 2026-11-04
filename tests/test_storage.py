import json
import sqlite3

from napcat_grok_bridge.storage import Store


def test_v1_events_table_is_migrated(tmp_path):
    store = Store(str(tmp_path))
    store.data_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """CREATE TABLE events (
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
            "INSERT INTO events(event_id, message_id, self_id, user_id, text, created_at) "
            "VALUES ('legacy', '1', '222222', 111111, 'old message', 1)"
        )

    store.initialize()
    row = store.next_pending_event()
    assert row["event_id"] == "legacy"
    assert json.loads(row["images"]) == []
