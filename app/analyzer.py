"""File-system watcher ➜ extract basic WAV metadata.
We rely on receiver's atomic move, so we can safely open the file as soon as
we see it; no more arbitrary sleep."""

import json, wave, datetime as dt, queue, signal, sys
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from . import config, utils


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
        print("[analyzer]", path, "→", out_path)

    except Exception as exc:
        print("[analyzer] error while processing", path, exc)



def manual_pass(path: Path):
    print("[analyzer] running manual pass...")
    
    # make sure all recordings are registered in DB
    for wav in path.glob("*.wav"):
        utils.ensure_row(wav.name)

    # find all unanalyzed files registered in the DB
    for filename in utils.get_unanalyzed():
        recording_path = config.RECORDINGS_DIR / filename

        # make sure the file actually exists
        if(recording_path.exists()):
            # enqeue only if not already queued
            if path not in list(analyze_queue.queue):
                analyze_queue.put(path)
                print(f"[analyzer] unanalyzed file found: {recording_path}. Adding it to the queue.")
                analyze_queue.put(recording_path)
        else:
            print(f"[analyzer] deleted unanalyzed file found: {recording_path}")
    
    print("[analyzer] manual pass done!")

def main():
    recordings_dir = str(config.RECORDINGS_DIR)
    manual_pass(Path(recordings_dir))

    observer = Observer()
    observer.schedule(Handler(), recordings_dir, recursive=False)
    observer.start()
    print("[analyzer] watching", recordings_dir)


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