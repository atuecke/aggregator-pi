"""Cleanup worker: when a recording has both analysis & upload done, delete the raw WAV."""

import os, time, logging, datetime as dt
from pathlib import Path

import redis
from . import config, utils, redis_utils

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()
log = logging.getLogger("app.cleanup")
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# Streams & groups
DONE_STREAM = "stream:done"
DONE_GROUP  = "cleanup-group"
CONSUMER        = "cleanup-1" 
BLOCK_MS        = 5000  # block up to 5s waiting for events

# Redis hash prefix for tracking per-filename state
HASH_PREFIX = "cleanup:status:"


def _init_group():
    try:
        r.xgroup_create(DONE_STREAM, DONE_GROUP, id="0-0", mkstream=True)
    except redis.exceptions.ResponseError:
        pass

def main():
    _init_group()
    while True:
        item = redis_utils.claim_job(DONE_STREAM, DONE_GROUP, CONSUMER, block_ms=5000)
        if not item:
            continue
        msg_id, job = item
        filename = job["filename"]
        stage    = job["stage"]          # "analyzed" or "uploaded"
        hkey     = HASH_PREFIX + filename
        r.hset(hkey, stage, 1)           # mark stage reached
        flags    = r.hgetall(hkey)

        if flags.get("analyzed") and flags.get("uploaded"):
            try:
                if(config.DELETE_RECORDINGS):
                    (Path(config.RECORDINGS_DIR) / filename).unlink(missing_ok=True)
                    log.info("Deleted %s", filename)
            except Exception as exc:
                log.error("Delete failed for %s: %s", filename, exc)
            r.delete(hkey)               # cleanup hash

        redis_utils.ack_job(DONE_STREAM, DONE_GROUP, msg_id)   # XACK+XDEL

if __name__ == "__main__":
    main()
