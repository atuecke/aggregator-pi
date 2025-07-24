"""Centralised runtime settings - mix of YAML defaults + env-var overrides."""
import os
import yaml
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# 1) Load defaults from mounted YAML (if present)
# ──────────────────────────────────────────────────────────────────────────────
DEFAULT_CFG_PATH = os.getenv("CONFIG_PATH", "/etc/iot/settings.yaml")
_defaults = {}
if Path(DEFAULT_CFG_PATH).is_file():
    with open(DEFAULT_CFG_PATH, "rt") as fh:
        _defaults = yaml.safe_load(fh) or {}

# ──────────────────────────────────────────────────────────────────────────────
# 2) Helper to read an int, bool, or str:  ENV  →  YAML default  →  hardcoded
# ──────────────────────────────────────────────────────────────────────────────
def _env(name, cast=str, default=None):
    try:
        val = os.getenv(name)
        if val is not None:
            # handle various bool expressions in env vars
            if cast is bool:
                if isinstance(val, bool):
                    return val
                if isinstance(val, str):
                    v = val.strip().lower()
                    if v in {"1", "true", "t", "yes", "y"}: return True
                    if v in {"0", "false", "f", "no", "n"}: return False
                raise ValueError(f"Cannot interpret {val!r} as bool")
            return cast(val)
    except (ValueError, TypeError, KeyError) as e:
        raise ValueError(f"ENV var {name} could not be cast: {e}") from e
    # look in YAML (keys in lower_snake_case)
    try:
        snake = name.lower()
        if snake in _defaults:
            return _defaults[snake]
    except (ValueError, TypeError, KeyError) as e:
        raise ValueError(f"YAML default {snake} could not be cast: {e}") from e
    return default

# ──────────────────────────────────────────────────────────────────────────────
# 3) Settings
# ──────────────────────────────────────────────────────────────────────────────
AGGREGATOR_UUID         = _env("AGGREGATOR_UUID",      str,   _defaults.get("aggregator_uuid", "dev-pi"))
METRICS_INTERVAL_SEC    = _env("METRICS_INTERVAL_SEC", int,   30)
GENERATE_MOCK_AUDIO     = _env("GENERATE_MOCK_AUDIO",  lambda v: bool(int(v)), False)
ANALYZE_RECORDINGS = _env("ANALYZE_RECORDINGS", lambda v: bool(int(v)), True)
UPLOAD_RAW_TO_CLOUD = _env("UPLOAD_RAW_TO_CLOUD", lambda v: bool(int(v)), True)
DELETE_RECORDINGS = _env("DELETE_RECORDINGS", lambda v: bool(int(v)), True)

#  --- Receiver Settings ------------------------------------------------------
RECORDINGS_UPLOAD_ENDPOINT      = _env("RECORDINGS_UPLOAD_ENDPOINT", str, "/recordings_upload")
RECORDINGS_STREAM_ENDPOINT      = _env("RECORDINGS_STREAM_ENDPOINT", str, "/recordings_stream")
MAX_RECORDING_BYTES    = _env("MAX_RECORDING_BYTES", int,   2000000)
MAX_RECORDING_DURATION_SEC    = _env("MAX_RECORDING_DURATION_SEC", int,   20)
MIN_RECORDING_DURATION_SEC = _env("MIN_RECORDING_DURATION_SEC", int,   3)
CONCAT_SHORT_RECORDINGS = _env("CONCAT_SHORT_RECORDINGS", lambda v: bool(int(v)), True)
AUDIO_NUM_CHANNELS = _env("AUDIO_NUM_CHANNELS", int, 1)
AUDIO_SAMPLE_RATE = _env("AUDIO_SAMPLE_RATE", int, 48000)
AUDIO_BITS_PER_SAMPLE = _env("AUDIO_BITS_PER_SAMPLE", int, 16)

# --- I/O directories ---------------------------------------------------------
BASE_DIR          = Path(_env("BASE_DIR", str, "/data"))
RECORDINGS_DIR    = Path(_env("RECORDINGS_DIR", str, BASE_DIR / "recordings"))
RECORDINGS_TMP_DIR= Path(_env("RECORDINGS_TMP_DIR", str, BASE_DIR / "recordings_tmp"))
LOGS_DIR          = Path(_env("LOGS_DIR", str, BASE_DIR / "logs"))

# --- InfluxDB 3 connection ---------------------------------------------------
INFLUX_URL               = _env("INFLUX_URL",   str, None)
INFLUX_TOKEN             = _env("INFLUX_TOKEN", str, None)
INFLUX_ORG               = _env("INFLUX_ORG",   str, None)
INFLUX_RECORDINGS_BUCKET = _env("INFLUX_RECORDINGS_BUCKET", str, None)
INFLUX_ANALYSIS_TABLE    = _env("INFLUX_ANALYSIS_TABLE",    str, "analysis")
INFLUX_UPLOADS_TABLE     = _env("INFLUX_UPLOADS_TABLE",     str, "uploads")

# --- Object store -----------------------------------------------------------
RCLONE_REMOTE_BUCKET     = _env("RCLONE_REMOTE_BUCKET", str, "s3:my-bucket/data")

# ensure directories exist
for p in (RECORDINGS_DIR, RECORDINGS_TMP_DIR, LOGS_DIR):
    p.mkdir(parents=True, exist_ok=True)
