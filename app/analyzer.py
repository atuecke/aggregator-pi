"""File-system watcher ➜ extract basic WAV metadata.
We rely on receiver's atomic move, so we can safely open the file as soon as
we see it; no more arbitrary sleep."""

import json, wave, datetime as dt, signal, sys
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from . import config, utils

# event callback for when a new recording is found
class Handler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if(path.suffix.lower() == ".wav"):
            self.analyze(path)  # blocks until done

    def analyze(self, path: Path):
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
            print("[analyzer]", path, "→", out_path)

        except Exception as exc:
            print("[analyzer] error while processing", path, exc)

def initial_pass(handler: Handler, path: Path):
    print("[analyzer] running initial pass...")
    for wav in path.glob("*.wav"):
        handler.analyze(wav)
    print("[analyzer] initial pass done!")

def main():
    observer = Observer()
    handler = Handler()
    recordings_dir = str(config.RECORDINGS_DIR)

    observer.schedule(handler, recordings_dir, recursive=False)
    observer.start()
    print("[analyzer] watching", recordings_dir)

    initial_pass(handler, Path(recordings_dir))

    try:
        while True:
            time.sleep(1)
    finally:
        observer.stop()
        observer.join()

if __name__ == "__main__":
    import time
    main()