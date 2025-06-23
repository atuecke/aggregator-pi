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
log = logging.getLogger("ANALYZER")
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
            # enqeue only if not already queued
            if path not in list(analyze_queue.queue):
                analyze_queue.put(path)


def analyze(path: Path):
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
        
        # write to the json file
        out_path = config.ANALYSIS_DIR / f"{path.stem}.json"
        out_path.write_text(json.dumps(meta, indent=2))

        # update the SQLite DB
        utils.set_field(path.name, "analyzed", str(out_path))
        log.info("Analyzed %s → %s", path.name, out_path)

    except Exception as exc:
       log.exception("Error while processing %s", path.name)



def manual_pass(path: Path):
    log.info("Running manual pass for existing files")
    
    # make sure all recordings are registered in DB
    for wav in path.glob("*.wav"):
        utils.ensure_row(wav.name)

    # find all unanalyzed files registered in the DB
    for filename in utils.get_unanalyzed():
        recording_path = config.RECORDINGS_DIR / filename

        # make sure the file actually exists
        if(recording_path.exists()):
            # enqeue only if not already queued
            if recording_path not in list(analyze_queue.queue):
                log.info("Queueing previously‑unprocessed file: %s", recording_path)
                analyze_queue.put(recording_path)
        else:
            log.warning("DB entry references missing file: %s", recording_path)
    
    log.info("Manual pass complete")

def main():
    recordings_dir = str(config.RECORDINGS_DIR)
    manual_pass(Path(recordings_dir))

    observer = Observer()
    observer.schedule(Handler(), recordings_dir, recursive=False)
    observer.start()
    log.info("Watching %s", recordings_dir)


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