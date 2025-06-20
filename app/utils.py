"""Shared helpers: tiny SQLite layer so every micro-service can record progress.
Each row tracks the lifecycle of a single recording - from on-disk file ➜ analyzed ➜ uploaded ➜ published ➜ deleted."""
from __future__ import annotations
import json
import sqlite3
import contextlib
from pathlib import Path
from typing import Any, Dict

from . import config

# --- SQLite schema for recordings lifecycle tracking -----------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    filename TEXT PRIMARY KEY,
    analyzed TEXT,
    uploaded TEXT,
    published_analysis INTEGER,
    published_upload INTEGER,
    deleted INTEGER
);
"""

@contextlib.contextmanager
def get_conn():
    """Context manager for SQLite connection using WAL journaling."""
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        yield conn
    finally:
        conn.close()

# Initialize DB schema on import
with get_conn() as conn:
    conn.executescript(SCHEMA)

# --- high-level helpers ----------------------------------------------------

def ensure_row(filename: str) -> None:
    """Insert a new row if it doesn't exist."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO recordings(filename) VALUES(?)",
            (filename,)
        )
        conn.commit()


def set_field(filename: str, field: str, value: Any) -> None:
    """Update a single column for a given filename."""
    with get_conn() as conn:
        conn.execute(
            f"UPDATE recordings SET {field} = ? WHERE filename = ?",
            (value, filename)
        )
        conn.commit()


def row(filename: str) -> Dict[str, Any]:
    """Fetch the recording's row as a dict, or {} if not found."""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT * FROM recordings WHERE filename = ?",
            (filename,)
        )
        result = cur.fetchone()
    return dict(result) if result else {}

def get_listener_id_from_name(name: str):
    return name.split('_', 1)[0]