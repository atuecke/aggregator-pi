"""Centralised runtime settings - tweak via env-vars for prod."""
import os
from pathlib import Path

AGGREGATOR_UUID = os.getenv("AGGREGATOR_UUID")
METRICS_INTERVAL_SEC = os.getenv("METRICS_INTERVAL_SEC", 30)
PUBLISH_INTERVAL_SEC = os.getenv("PUBLISH_INTERVAL_SEC", 10)
GENERATE_MOCK_AUDIO = os.getenv("GENERATE_MOCK_AUDIO", "false")

# --- I/O directories ---------------------------------------------------------
BASE_DIR = Path(os.getenv("BASE_DIR", "/data"))
RECORDINGS_DIR      = BASE_DIR / "recordings"
RECORDINGS_TMP_DIR  = BASE_DIR / "recordings_tmp"  # receiver writes here first
ANALYSIS_DIR        = BASE_DIR / "analysis"   # analyzer output
UPLOADS_DIR         = BASE_DIR / "uploads"   # uploader output
LOGS_DIR            = BASE_DIR / "logs"
METRICS_LOG_PATH    = LOGS_DIR / "metrics.log"
DB_PATH             = BASE_DIR / "jobs.sqlite3"


# --- InfluxDB 3 connection ---------------------------------------------------
INFLUX_URL    = os.getenv("INFLUX_URL",   "http://influxdb:8181")
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN")
INFLUX_ORG    = os.getenv("INFLUX_ORG")
INFLUX_RECORDINGS_BUCKET = os.getenv("INFLUX_RECORDINGS_BUCKET")
INFLUX_METRICS_BUCKET = os.getenv("INFLUX_METRICS_BUCKET")
INFLUX_ANALYSIS_TABLE = os.getenv("INFLUX_ANALYSIS_TABLE", "analysis")
INFLUX_UPLOADS_TABLE = os.getenv("INFLUX_UPLOADS_TABLE", "uploads")


# --- Listener Connection ----------------------------------------------------
RECORDINGS_ENDPOINT = os.getenv("RECORDINGS_ENDPOINT", "/recording_endpoint")

# --- Object store -----------------------------------------------------------
RCLONE_REMOTE_BUCKET = os.getenv("RCLONE_REMOTE_BUCKET", "s3:my-bucket/data")

for p in (RECORDINGS_DIR, RECORDINGS_TMP_DIR, ANALYSIS_DIR, UPLOADS_DIR, LOGS_DIR):
    p.mkdir(parents=True, exist_ok=True)