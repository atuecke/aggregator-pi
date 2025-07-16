"""Redis-based analyzer worker - pulls jobs from a Redis Stream, runs BirdNET,
publishes results to the next stream, and ACKs or requeues on failure."""

import os, time, json, wave, datetime as dt, logging
from pathlib import Path

from birdnetlib import Recording
from birdnetlib.analyzer import Analyzer

from . import config, utils
from . import redis_utils


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()  # Initialise global logging once per process
WORKER_ID = int(os.getenv("WORKER_ID", "0"))   # set by Supervisor
log = logging.getLogger(f"app.analyzer_{WORKER_ID}")
# ---------------------------------------------------------------------------


# Redis stream settings
STREAM   = "stream:analyze"
GROUP    = "analyze-group"
CONSUMER = f"worker-{WORKER_ID}"
BLOCK_MS = 5000  # 5-second blocking read
MAX_ATTEMPTS = 2


_analyzer = Analyzer() # model loads once here 


def handle_job(msg_id: str, job: dict) -> None:
    filename    = job["filename"]
    local_path  = job["local_path"]
    listener_id = job["listener_id"]
    recorded_timestamp = job["recorded_timestamp"]
    attempts    = int(job.get("attempts", 0))

    path = Path(local_path)
    log.debug("Worker %s analyzing %s (attempt %d)", WORKER_ID, filename, attempts)

    try:
        with wave.open(str(path)) as wf:
            duration = wf.getnframes() / wf.getframerate()

        rec = Recording(
            _analyzer,
            str(path),
            lat=0.0, lon=0.0,
            date=dt.datetime.utcnow(),
            min_conf=0.25,
        )
        t0 = time.perf_counter()
        rec.analyze()
        elapsed = (time.perf_counter() - t0) * 1000
        efficiency=elapsed/(duration*1000)
        log.debug("BirdNET on worker %s took %.0f ms to analyze %d seconds (%.2f efficiency): %s", WORKER_ID, elapsed, duration, efficiency, filename)

        meta = {
            "filename": filename,
            "recorded_timestamp": recorded_timestamp,
            "analyzed_timestamp": dt.datetime.utcnow().isoformat() + "Z",
            "duration_sec": duration,
            "listener_id": listener_id,
            "detections": rec.detections,
        }

        redis_utils.enqueue_job("stream:publish_analysis", meta)
        redis_utils.enqueue_job("stream:done", {"filename": filename, "listener_id": listener_id, "stage": "analyzed"})
        if not config.UPLOAD_RAW_TO_CLOUD:
                # set the upload to done aswell so that the cleanup process runs properly
                redis_utils.enqueue_job("stream:done", {"filename": filename, "listener_id": listener_id, "stage": "uploaded"})
        redis_utils.ack_job(STREAM, GROUP, msg_id)  # success – remove from stream
        log.info(
            "Analyzed %s in %.0f ms, %d detections",
            filename, elapsed, len(rec.detections)
        )

    except Exception as exc:
        log.error("Analysis failed on %s: %s", filename, exc)
        if attempts + 1 >= MAX_ATTEMPTS:
            # dead-letter after too many tries
            job.update({"error": str(exc), "failed_at": dt.datetime.utcnow().isoformat()+"Z"})
            redis_utils.enqueue_job(f"{STREAM}:dead", job)
            redis_utils.ack_job(STREAM, GROUP, msg_id)
        else:
            # requeue with back-off
            job["attempts"] = attempts + 1
            redis_utils.enqueue_job(STREAM, job)
            redis_utils.ack_job(STREAM, GROUP, msg_id)
            time.sleep(1 * (attempts + 1))  # simple linear back-off


def main() -> None:
    log.info("Analyzer worker %s listening on Redis stream %s", WORKER_ID, STREAM)
    while True:
        item = redis_utils.claim_job(STREAM, GROUP, CONSUMER, block_ms=BLOCK_MS)
        if not item:
            continue  # timed out – loop back
        msg_id, job = item
        handle_job(msg_id, job)

if __name__ == "__main__":
    if(config.ANALYZE_RECORDINGS):
        main()
    else:
        log.info("Analyzing recordings has been disabled. Re-enable it in settings to start analyzing recordings with birdnet")
        time.sleep(5) # Avoid supervisord running check failing