import json
import subprocess
import datetime as dt
import time
import queue
from pathlib import Path
from . import config, utils, redis_utils


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()  # Initialise global logging once per process
import logging
log = logging.getLogger("app.uploader")
# ---------------------------------------------------------------------------

# Redis stream / consumer group settings
STREAM    = "stream:upload"
GROUP     = "upload-group"
CONSUMER  = "uploader-1"
BLOCK_MS  = 5000  # wait up to 5 s

def upload_job_loop():
    log.info("Uploader worker %s starting, listening on %s", CONSUMER, STREAM)
    while True:
        item = redis_utils.claim_job(STREAM, GROUP, CONSUMER, block_ms=BLOCK_MS)
        if not item:
            # timed out, loop back to keep the consumer alive
            continue

        msg_id, job = item
        filename    = job.get("filename")
        local_path  = job.get("local_path")
        listener_id = job.get("listener_id")
        path = Path(local_path)

        log.debug("Attempting to upload %s (msg %s)", filename, msg_id)
        try:
            remote_path = (
                f"{config.RCLONE_REMOTE_BUCKET}/"
                f"{config.AGGREGATOR_UUID}/"
                f"{listener_id}/{filename}"
            )
            result = subprocess.run(
                ["rclone", "copyto", str(path), remote_path],
                capture_output=True, text=True
            )

            if result.returncode != 0:
                raise RuntimeError(f"rclone error: {result.stderr.strip()}")

            # success → enqueue publish_upload
            payload = {
                "filename": filename,
                "remote": remote_path,
                "uploaded_at": dt.datetime.utcnow().isoformat() + "Z",
                "listener_id": listener_id,
            }
            redis_utils.enqueue_job("stream:publish_upload", payload)
            redis_utils.enqueue_job("stream:done", {"filename": filename, "listener_id": listener_id, "stage": "uploaded"})
            log.info("Uploaded %s → %s", filename, remote_path)

            # ACK & remove from stream
            redis_utils.ack_job(STREAM, GROUP, msg_id)

        except Exception as exc:
            log.error("Upload failed for %s: %s", filename, exc)

            # dead-letter: record failure and remove from pending
            dead_payload = {
                **job,
                "error": str(exc),
                "failed_at": dt.datetime.utcnow().isoformat() + "Z",
            }
            redis_utils.enqueue_job(f"{STREAM}:dead", dead_payload)
            redis_utils.ack_job(STREAM, GROUP, msg_id)

            # short pause to avoid tight-loop on repeated errors
            time.sleep(1)


# def manual_pass():
#     log.info("Running manual pass for uploader")
#     path = config.UPLOADS_DIR
    
#     # make sure all recordings are registered in DB
#     for wav in path.glob("*.wav"):
#         if not utils.job_exists(wav.name, "upload"):
#             utils.create_job(wav.name, "upload", {"local_path": str(wav)})
#             upload_queue.put(wav)
    
#     log.info("Uploader manual pass complete")


def main():
    # Delay start slightly so Redis is ready under supervisord
    time.sleep(2)
    upload_job_loop()

if __name__ == "__main__":
    main()