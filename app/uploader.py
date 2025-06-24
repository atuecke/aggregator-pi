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
log = logging.getLogger("app.uploader")
# ---------------------------------------------------------------------------


# FIFO queue for paths to upload
upload_queue: queue.Queue[Path] = queue.Queue()

class Handler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if(path.suffix.lower() == ".wav"):
            log.debug("Found new recording %s, adding to the upload queue", path.name)
            # enqeue only if not already queued
            if path not in list(upload_queue.queue):
                upload_queue.put(path)



def upload(path: Path):
    log.debug("Attempting to upload recording %s", path.name)
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
        utils.set_job_status_by_filename_type(path.name, "upload", "done", pointer)
        utils.create_job(path.name, "publish_upload", pointer)
        log.info("Uploaded %s → %s", path, remote_path)
        # path.unlink(missing_ok=True)
    else:
        utils.set_job_status_by_filename_type(path.name, "upload", "error", {"stderr": result.stderr})
        log.error("Upload failed for %s: %s", path, result.stderr)


def manual_pass():
    log.info("Running manual pass for uploader")
    path = config.UPLOADS_DIR
    
    # make sure all recordings are registered in DB
    for wav in path.glob("*.wav"):
        if not utils.job_exists(wav.name, "upload"):
            utils.create_job(wav.name, "upload", {"local_path": str(wav)})
            upload_queue.put(wav)
    
    log.info("Uploader manual pass complete")


def main():
    manual_pass()
    
    observer = Observer()
    observer.schedule(Handler(), str(config.RECORDINGS_DIR), recursive=False)
    observer.start()
    log.info("Uploader Watchdog %s", str(config.RECORDINGS_DIR))

    try:
        while True:
            path = upload_queue.get()
            upload(path)
            upload_queue.task_done
    finally:
        observer.stop(); observer.join()

if __name__ == "__main__":
    main()