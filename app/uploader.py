"""Uploads recordings to Chameleon Object Store via rclone.
Blocking handler: Watchdog waits until each upload finishes before
dispatching the next file event. Simple, single-threaded, reliable."""

import json
import subprocess
import datetime as dt
import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from . import config, utils

class Handler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        self.upload(Path(event.src_path))

    def upload(self, path: Path):
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
            print("[uploader]", path, "→", remote_path)
            path.unlink(missing_ok=True)
            utils.set_field(path.name, "deleted", 1)
        else:
            print("[uploader] FAILED", path, result.stderr)


def initial_pass(handler: Handler, path: Path):
    print("[uploader] running initial pass...")
    for wav in path.glob("*.wav"):
        handler.upload(wav)
    print("[uploader] initial pass done!")


def main():
    observer = Observer()
    handler = Handler()
    recordings_dir = str(config.RECORDINGS_DIR)

    observer.schedule(handler, recordings_dir, recursive=False)
    observer.start()
    print("[uploader] watching", recordings_dir)

    initial_pass(handler, Path(recordings_dir))

    try:
        while True:
            time.sleep(1)
    finally:
        observer.stop(); observer.join()

if __name__ == "__main__":
    main()