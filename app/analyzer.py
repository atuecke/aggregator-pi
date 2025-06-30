"""File-system watcher ➜ extract basic WAV metadata.
We rely on receiver's atomic move, so we can safely open the file as soon as
we see it; no more arbitrary sleep."""

import json, wave, datetime as dt, queue, signal, sys
from pathlib import Path
from watchdog.events import FileSystemEventHandler
from . import config, utils
from birdnetlib import Recording
from birdnetlib.analyzer import Analyzer
import time
import os



WORKER_ID = int(os.getenv("WORKER_ID", "0"))   # set by Supervisor
_analyzer = Analyzer()                         # model loads once here 

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()  # Initialise global logging once per process
import logging
log = logging.getLogger(f"app.analyzer_{WORKER_ID}")
# ---------------------------------------------------------------------------


# FIFO queue for paths to analyze
analyze_queue: queue.Queue[Path] = queue.Queue()


def analyze_job(job: dict):
    filename = job["filename"]
    path = Path(config.RECORDINGS_DIR / filename)

    log.debug("Starting analysis for %s on worker %s", filename, WORKER_ID)
    utils.set_job_status_by_filename_type(filename, "analyze", "running")

    try:
        with wave.open(str(path)) as wf:
            duration = wf.getnframes() / wf.getframerate()

        # parse recorded timestamp from filename (format: rec_YYYYMMDDTHHMMSSZ.wav)
        rec_str = path.stem.split("_", 1)[1]
        recorded_dt = dt.datetime.strptime(rec_str, "%Y%m%dT%H%M%SZ")
        recorded_timestamp = recorded_dt.isoformat() + "Z"
        analyzed_timestamp = dt.datetime.utcnow().isoformat() + "Z"

        start = time.perf_counter()
        rec = Recording(
            _analyzer,
            str(path),
            lat=0.0,
            lon=0.0,
            date=dt.datetime.utcnow(),
            min_conf=0.25,
        )
        rec.analyze()
        log.debug("BirdNET on worker %s took %dms to analyze %d seconds: %s", WORKER_ID, (time.perf_counter() - start) * 1000, duration, filename)

        meta = {
            "filename": filename,
            "recorded_timestamp": recorded_timestamp,
            "analyzed_timestamp": analyzed_timestamp,
            "duration_sec": duration,
            "listener_id": utils.get_listener_id_from_name(path.name),
            "detections": rec.detections,
        }

        utils.set_job_status_by_filename_type(
            filename, "analyze", "done", meta
        )
        utils.create_job(
            filename, job["listener_id"], "publish_analysis", meta
        )
        log.info("Analyzed %s (payload stored in DB) and found %d detections", filename, len(rec.detections))
    
    except Exception as exc:
        utils.set_job_status_by_filename_type(
            filename, "analyze", "error", {"err": str(exc)}
        )
        log.exception("Worker %s failed on %s", WORKER_ID, filename)


def main():
    log.info("Analyzer worker %s ready", WORKER_ID)

    while True:
        log.debug("Analyzer worker %s polling for pending jobs...", WORKER_ID)
        job = utils.pop_pending_job("analyze")
        if not job:
            time.sleep(2.0)
            continue
        analyze_job(job)

if __name__ == "__main__":
    main()