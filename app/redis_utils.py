import os, json, time
import redis
import logging
from . import utils
import inspect
from pathlib import Path


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()  # Initialise global logging once per process
log = logging.getLogger("app.redis_utils")
# ---------------------------------------------------------------------------

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
STREAM_TTL = int(os.getenv("STREAM_MAXLEN", "10000"))  # trim length

r = redis.Redis(host=REDIS_HOST, decode_responses=True)

# ---------------------------------------------------------------------------
# Logging helper for Redis calls
# ---------------------------------------------------------------------------
def _log_redis(label: str, start: float) -> None:
    """Log elapsed time for a Redis operation, skipping internal calls."""
    stack = inspect.stack()
    caller_frame = stack[2]
    caller_file = Path(caller_frame.filename).name
    if caller_file == "utils_redis.py":
        return
    elapsed_ms = (time.perf_counter() - start) * 1000
    log.debug("REDIS %s called from %s took %.2f ms", label, caller_file, elapsed_ms)


# ---------------------------------------------------------------------------
# Stream helpers
# ---------------------------------------------------------------------------
def enqueue_job(stream: str, payload: dict[str, str | int]) -> None:
    """Push a new job onto a Redis stream."""
    start = time.perf_counter()
    # Redis fields must be strings
    data = {k: json.dumps(v) for k, v in payload.items()}
    r.xadd(stream, data, maxlen=STREAM_TTL, approximate=True)
    _log_redis(f"enqueue_job {stream}", start)

def claim_job(
    stream: str,
    group: str,
    consumer: str,
    block_ms: int = 5000
) -> tuple[str, dict] | None:
    """Blocking-claim one pending job from the given consumer-group."""
    start = time.perf_counter()
    # ensure the group exists
    try:
        r.xgroup_create(stream, group, id="0-0", mkstream=True)
    except redis.exceptions.ResponseError:
        pass  # already created

    resp = r.xreadgroup(group, consumer, {stream: ">"}, count=1, block=block_ms)
    if not resp:
        _log_redis(f"claim_job {stream} (timeout)", start)
        return None

    _, msgs = resp[0]
    msg_id, fields = msgs[0]
    job = {k: json.loads(v) for k, v in fields.items()}
    _log_redis(f"claim_job {stream}", start)
    return msg_id, job

def ack_job(stream: str, group: str, msg_id: str) -> None:
    """Acknowledge and remove a processed message from the given Redis stream."""
    start = time.perf_counter()
    r.xack(stream, group, msg_id)
    r.xdel(stream, msg_id)
    _log_redis(f"ack_job {stream}", start)
