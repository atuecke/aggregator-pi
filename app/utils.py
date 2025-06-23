"""Shared helpers: tiny SQLite layer so every micro-service can record progress.
Each row tracks the lifecycle of a single recording - from on-disk file ➜ analyzed ➜ uploaded ➜ published ➜ deleted."""
from __future__ import annotations
import json
import logging
import logging.config
import os
import yaml
import sqlite3
import contextlib
from pathlib import Path
from typing import Any, Dict, List, Tuple
import time

from . import config


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def setup_logging(
    default_path: str = "/etc/iot/logging.yaml",
    env_key: str = "LOG_CFG",
    default_level: int = logging.INFO,
) -> None:
    """Initialise application-wide logging.

    Order of precedence::
        1. Path in $LOG_CFG env-var (set at runtime).
        2. *default_path* baked into the Docker image.
        3. Fallback to :pyfunc:`logging.basicConfig` with *default_level*.
    """
    cfg_path = Path(os.getenv(env_key, default_path))
    if cfg_path.exists():
        with cfg_path.open("rt") as fh:
            cfg_dict = yaml.safe_load(fh.read())
        logging.config.dictConfig(cfg_dict)
    else:
        logging.basicConfig(level=default_level)


# ---------------------------------------------------------------------------
# SQLite schema for recordings lifecycle tracking
# ---------------------------------------------------------------------------

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
    conn = sqlite3.connect(config.DB_PATH, timeout=12)
    conn.execute("PRAGMA journal_mode=WAL;")
    # conn.execute("PRAGMA synchronous=NORMAL;") # first couple inserts/queries take a very long time and this reduces it, but tiny risk on power-loss
    try:
        yield conn
    finally:
        conn.close()

# Initialize DB schema on import
with get_conn() as conn:
    conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# High‑level helpers
# ---------------------------------------------------------------------------

def ensure_row(filename: str) -> None:
    """Insert a new row if it doesn't exist."""
    start = time.perf_counter()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO recordings(filename) VALUES(?)",
            (filename,)
        )
        conn.commit()
    logging.getLogger(__name__).debug(
        "SQLite ensure_row for '%s' took %.2f ms", filename, (time.perf_counter() - start) * 1000
    )

def set_field(filename: str, field: str, value: Any) -> None:
    """Update a single column for a given filename."""
    start = time.perf_counter()
    with get_conn() as conn:
        conn.execute(
            f"UPDATE recordings SET {field} = ? WHERE filename = ?",
            (value, filename)
        )
        conn.commit()
    logging.getLogger(__name__).debug(
        "SQLite update '%s' for '%s' took %.2f ms", field, filename, (time.perf_counter() - start) * 1000
    )


def row(filename: str) -> Dict[str, Any]:
    """Fetch the recording's row as a dict, or {} if not found."""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT * FROM recordings WHERE filename = ?",
            (filename,)
        )
        result = cur.fetchone()
    return dict(result) if result else {}

def get_unanalyzed() -> list[str]:
    """Return list[filename] that haven't been marked as analyzed in the DB"""
    
    with get_conn() as conn:
        cur = conn.execute("SELECT filename FROM recordings WHERE analyzed IS NULL")
        return [row[0] for row in cur.fetchall()]
    
def get_unuploaded() -> list[str]:
    """Return list[filename] that haven't been marked as uploaded in the DB"""
    
    with get_conn() as conn:
        cur = conn.execute("SELECT filename FROM recordings WHERE uploaded IS NULL")
        return [row[0] for row in cur.fetchall()]

def get_unpublished_analysis() -> List[Path]:
    """
    Return a list of result JSON Paths in RESULTS_DIR that haven't been published to InfluxDB yet.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT analyzed FROM recordings "
            "WHERE analyzed IS NOT NULL AND published_analysis IS NULL"
        )
        return [Path(row[0]) for row in cur.fetchall()]
    
def get_unpublished_upload() -> List[Path]:
    """
    Return a list of result JSON Paths in UPLOAD_DIR that haven't been published to InfluxDB yet.
    """
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT uploaded FROM recordings "
            "WHERE uploaded IS NOT NULL AND published_upload IS NULL"
        )
        return [Path(row[0]) for row in cur.fetchall()]


def get_listener_id_from_name(name: str):
    return name.split('_', 1)[0]