from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .config import settings


_TTL_SECONDS = 7 * 24 * 3600


def _conn() -> sqlite3.Connection:
    Path(settings.cache_db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.cache_db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, ts INTEGER)"
    )
    return conn


def get(key: str) -> dict | list | None:
    with _conn() as c:
        row = c.execute("SELECT v, ts FROM kv WHERE k = ?", (key,)).fetchone()
    if not row:
        return None
    value, ts = row
    if time.time() - ts > _TTL_SECONDS:
        return None
    return json.loads(value)


def put(key: str, value: dict | list) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO kv (k, v, ts) VALUES (?, ?, ?)",
            (key, json.dumps(value, ensure_ascii=False), int(time.time())),
        )
