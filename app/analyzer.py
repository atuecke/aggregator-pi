"""File-system watcher ➜ extract basic WAV metadata.
We rely on receiver's atomic move, so we can safely open the file as soon as
we see it; no more arbitrary sleep."""

import json, wave, datetime as dt, queue, signal, sys
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from . import config, utils

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()  # Initialise global logging once per process
import logging
log = logging.getLogger("app.analyzer")
# ---------------------------------------------------------------------------


# FIFO queue for paths to analyze
analyze_queue: queue.Queue[Path] = queue.Queue()


# event callback for when a new recording is found
class Handler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if(path.suffix.lower() == ".wav"):
            log.debug("Found new recording %s, adding to the analyze queue", path.name)
            # enqeue only if not already queued
            if path not in list(analyze_queue.queue):
                analyze_queue.put(path)


def analyze(path: Path):
    log.debug("Attempting to analyze recording %s", path.name)
    utils.set_job_status_by_filename_type(path.name, "analyze", "running")

    try:
        # parse recorded timestamp from filename (format: rec_YYYYMMDDTHHMMSSZ.wav)
        rec_str = path.stem.split("_", 1)[1]
        recorded_dt = dt.datetime.strptime(rec_str, "%Y%m%dT%H%M%SZ")
        recorded_timestamp = recorded_dt.isoformat() + "Z"
        analyzed_timestamp = dt.datetime.utcnow().isoformat() + "Z"

        # extract metadata
        with wave.open(str(path)) as wf:
            duration = wf.getnframes() / wf.getframerate()
        
        meta = {
            "filename": path.name,
            "recorded_timestamp": recorded_timestamp,
            "analyzed_timestamp": analyzed_timestamp,
            "duration_sec": duration,
            "listener_id": utils.get_listener_id_from_name(path.name)
        }
        utils.set_job_status_by_filename_type(path.name, "analyze", "done", meta)
        utils.create_job(path.name, "publish_analysis", meta)
        
        log.info("Analyzed %s (payload stored in DB)", path.name)

    except Exception as exc:
       utils.set_job_status_by_filename_type(path.name, "analyze", "error", {"error": str(exc)})
       log.exception("Failed to analyze %s", path.name)



def manual_pass():
    log.info("Running manual pass for analyzer")
    path = config.RECORDINGS_DIR
    
    # make sure all recordings are registered in DB
    for wav in path.glob("*.wav"):
        if not utils.job_exists(wav.name, "analyze"):
            utils.create_job(wav.name, "analyze", {"local_path": str(wav)})
            analyze_queue.put(wav)
    
    log.info("Analyzer manual pass complete")

def main():
    manual_pass()

    observer = Observer()
    observer.schedule(Handler(), str(config.RECORDINGS_DIR), recursive=False)
    observer.start()
    log.info("Analyzer Watchdog %s", str(config.RECORDINGS_DIR))


    try:
        while True:
            path = analyze_queue.get()
            analyze(path)
            analyze_queue.task_done
    finally:
        observer.stop()
        observer.join()

if __name__ == "__main__":
    main()