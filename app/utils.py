"""Shared helpers: tiny SQLite layer so every micro-service can record progress.
Each row tracks the lifecycle of a single recording - from on-disk file ➜ analyzed ➜ uploaded ➜ published ➜ deleted."""
from __future__ import annotations
import json
import logging
import logging.config
import os
import yaml
import inspect
import sqlite3
import contextlib
from pathlib import Path
from typing import Any, Dict, List, Tuple
import time
import datetime as dt
from functools import lru_cache

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

logger = logging.getLogger("app.utils")

# ---------------------------------------------------------------------------
# SQLite schema for recordings lifecycle tracking
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  filename      TEXT NOT NULL,
  type          TEXT NOT NULL,         -- 'analyze', 'upload', 'publish_analysis', 'publish_upload'
  payload       JSON,                  -- any extra data
  status        TEXT NOT NULL DEFAULT 'pending',  -- 'pending' → 'running' → 'done' → 'error'
  created_at    TEXT NOT NULL,
  updated_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_type ON jobs(status, type);
CREATE INDEX IF NOT EXISTS idx_jobs_filename_type ON jobs(filename, type);
"""

def _persistent_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=12, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;") # first couple inserts/queries take a very long time and this reduces it, but tiny risk on power-loss
    return conn

@contextlib.contextmanager
def get_conn():
    """Context manager for SQLite connection using WAL journaling.
    Reuse a single connection instead of opening/closing every query."""

    conn = _persistent_conn()
    try:
        yield conn
    finally:
        pass

# Initialize DB schema on import
with get_conn() as conn:
    conn.executescript(SCHEMA)

# ---------------------------------------------------------------------------
# Helper for profiling
# ---------------------------------------------------------------------------

def _log_sql(label: str, start: float) -> None:
    stack = inspect.stack()
    caller_frame = stack[2]
    caller_file = Path(caller_frame.filename).name
    if(caller_file == "utils.py"): return
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.debug("SQL %s called from %s took %.2f ms", label, caller_file, elapsed_ms)


# ---------------------------------------------------------------------------
# Job-level helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return dt.datetime.utcnow().isoformat() + "Z"

def job_exists(filename: str, job_type: str, status: str | None = None) -> bool:
    sql = "SELECT 1 FROM jobs WHERE filename=? AND type=?"
    params: list[Any] = [filename, job_type]
    if status:
        sql += " AND status=?"
        params.append(status)
    
    start = time.perf_counter()
    with get_conn() as conn:
        cur = conn.execute(sql + " LIMIT 1", tuple(params))
        _log_sql(f"job_exists for {filename} {job_type}", start)
        return cur.fetchone() is not None
    

def create_job(filename: str, job_type: str, payload: dict[str, Any] | None = None) -> int:
    """Insert a *pending* job if one isn't already pending/done for that step."""
    start = time.perf_counter()
    if job_exists(filename, job_type, status="pending") or job_exists(filename, job_type, status="running"):
        logger.warning("Attempted to create an already existing job %s %s", filename, job_type)
        return -1  # caller may ignore
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO jobs(filename, type, payload, status, created_at, updated_at)"\
            " VALUES(?,?,?,?,?,?)",
            (
                filename,
                job_type,
                json.dumps(payload or {}),
                "pending",
                _now(),
                _now(),
            ),
        )
        conn.commit()
    
    _log_sql(f"create_job for {filename} {job_type}", start)
    return cur.lastrowid
    

def set_job_status_by_filename_type(
    filename: str,
    job_type: str,
    status: str,
    payload_update: dict[str, Any] | None = None,
) -> None:
    """Update status (and optionally payload) for a job identified by (filename, type)
    that is *not already done*. We operate on the most recent matching row."""
    start = time.perf_counter()
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, payload FROM jobs WHERE filename=? AND type=? ORDER BY id DESC LIMIT 1",
            (filename, job_type),
        )
        row = cur.fetchone()
        if row is None:
            logger.warning("Attempted to set status of an undefined job %s %s", filename, job_type)
            return
        job_id, existing_payload = row
        if payload_update:
            try:
                merged = {**json.loads(existing_payload or "{}"), **payload_update}
            except Exception:
                merged = payload_update  # fallback – shouldn't happen
            payload_json = json.dumps(merged)
            conn.execute(
                "UPDATE jobs SET status=?, payload=?, updated_at=? WHERE id=?",
                (status, payload_json, _now(), job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET status=?, updated_at=? WHERE id=?",
                (status, _now(), job_id),
            )
        conn.commit()
    _log_sql(f"set_job_status of {filename} {job_type} to {status}", start)


def get_pending_jobs(job_type: str) -> List[Dict[str, Any]]:
    start = time.perf_counter()
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT id, filename, payload FROM jobs WHERE type=? AND status='pending' ORDER BY id",
            (job_type,),
        )
        cols = [c[0] for c in cur.description]
        _log_sql(f"get_pending_jobs for {job_type}", start)
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    
        

# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

def get_listener_id_from_name(name: str):
    return name.split('_', 1)[0]