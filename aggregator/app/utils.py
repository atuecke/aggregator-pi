"""Shared helpers: tiny SQLite layer so every micro-service can record progress.
Each row tracks the lifecycle of a single recording - from on-disk file ➜ analyzed ➜ uploaded ➜ published ➜ deleted."""
from __future__ import annotations
import json
import logging
import logging.config
import os
import yaml
from pathlib import Path
from typing import Any, Dict, List, Tuple
import time
import datetime as dt
from functools import lru_cache
import threading

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
# Misc helpers
# ---------------------------------------------------------------------------

def get_listener_id_from_name(name: str):
    return name.split('_', 1)[0]