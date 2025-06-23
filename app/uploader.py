"""Uploads recordings to Chameleon Object Store via rclone.
Blocking handler: Watchdog waits until each upload finishes before
dispatching the next file event. Simple, single-threaded, reliable."""

import json
import subprocess
import datetime as dt
import time
import queue
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from . import config, utils


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()  # Initialise global logging once per process
import logging
log = logging.getLogger("UPLOADER")
# ---------------------------------------------------------------------------


# FIFO queue for paths to upload
upload_queue: queue.Queue[Path] = queue.Queue()

class Handler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if(path.suffix.lower() == ".wav"):
            # enqeue only if not already queued
            if path not in list(upload_queue.queue):
                upload_queue.put(path)



def upload(path: Path):
    remote_path = f"{config.RCLONE_REMOTE_BUCKET}/{config.AGGREGATOR_UUID}/{path.name.split('_', 1)[0]}/{path.name}"
    result = subprocess.run(
        ["rclone", "copyto", str(path), remote_path],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        pointer = {
            "filename": path.name,
            "remote": remote_path,
            "uploaded_at": dt.datetime.utcnow().isoformat() + "Z",
            "listener_id": utils.get_listener_id_from_name(path.name)
        }
        out_path = config.UPLOADS_DIR / f"{path.stem}.json"
        out_path.write_text(json.dumps(pointer, indent=2))
        utils.set_field(path.name, "uploaded", str(out_path))
        log.info("Uploaded %s → %s", path, remote_path)
        path.unlink(missing_ok=True)
        utils.set_field(path.name, "deleted", 1)
    else:
        log.error("Upload failed for %s: %s", path, result.stderr)


def manual_pass(path: Path):
    log.info("Running manual pass for uploader")

    # make sure all recordings are registered in DB
    for wav in path.glob("*.wav"):
        utils.ensure_row(wav.name)
    
    # find all unuploaded files registered in the DB
    for filename in utils.get_unuploaded():
        recording_path = config.UPLOADS_DIR / filename

        # make sure the file actually exists
        if(recording_path.exists()):
            # enqeue only if not already queued
            if recording_path not in list(upload_queue.queue):
                log.info("Queueing unuploaded file: %s", recording_path)
                upload_queue.put(recording_path)
        else:
            log.warning("Missing file for DB entry: %s", recording_path)
    
    log.info("Uploader manual pass complete")


def main():
    recordings_dir = str(config.RECORDINGS_DIR)
    manual_pass(Path(recordings_dir))
    
    observer = Observer()
    observer.schedule(Handler(), recordings_dir, recursive=False)
    observer.start()
    log.info("Uploader watching %s", recordings_dir)

    try:
        while True:
            path = upload_queue.get()
            upload(path)
            upload_queue.task_done
    finally:
        observer.stop(); observer.join()

if __name__ == "__main__":
    main()