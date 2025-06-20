"""Push analyzer & uploader JSON docs into InfluxDB 3.
Handler runs uploads/publish sequentially; blocking inside `on_created`."""

import json
import time
import queue
from threading import Thread
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from influxdb_client_3 import InfluxDBClient3, Point
from . import config, utils
import datetime as dt

# connect to InfluxDB3
client = InfluxDBClient3(
    host=config.INFLUX_URL,
    token=config.INFLUX_TOKEN,
    org=config.INFLUX_ORG,
    database=config.INFLUX_RECORDINGS_BUCKET,
)

# FIFO queue for paths to publish
analysis_publish_queue = queue.Queue()
uploads_publish_queue = queue.Queue()


class AnalysisHandler(FileSystemEventHandler):
    def on_created(self, event):
        """Called by Watchdog when a JSON result appears."""
        if event.is_directory:
            return
        path = Path(event.src_path)
        if(path.suffix.lower() == ".json"):
            # enqeue only if not already queued
            if path not in list(analysis_publish_queue.queue):
                analysis_publish_queue.put(path)

# event callback for when a new json is found
class UploadHandler(FileSystemEventHandler):
    def on_created(self, event):
        """Called by Watchdog when a JSON upload pointer appears."""
        if event.is_directory:
            return
        path = Path(event.src_path)
        if(path.suffix.lower() == ".json"):
            # enqeue only if not already queued
            if path not in list(uploads_publish_queue.queue):
                uploads_publish_queue.put(path)


def publish_analysis(path: Path):
    data = json.loads(path.read_text())
    point = (
        Point(config.INFLUX_ANALYSIS_TABLE)
        .tag("filename", data.get("filename"))
        .tag("aggregator_uuid", config.AGGREGATOR_UUID)
        .tag("listener_id", data.get("listener_id"))
        .field("data", json.dumps(data))
        .time(data.get("published_timestamp", dt.datetime.utcnow().isoformat() + "Z"))
    )
    client.write(point)
    utils.set_field(data.get("filename"), "published_analysis", 1)
    print(f"[publisher] published analysis: {config.INFLUX_RECORDINGS_BUCKET}/{config.INFLUX_ANALYSIS_TABLE}", path)


def publish_upload(path: Path):
    data = json.loads(path.read_text())
    point = (
        Point(config.INFLUX_UPLOADS_TABLE)
        .tag("filename", data.get("filename"))
        .tag("aggregator_uuid", config.AGGREGATOR_UUID)
        .tag("listener_id", data.get("listener_id"))
        .field("remote", data.get("remote"))
        .field("uploaded_at", data.get("uploaded_at"))
        .field("data", json.dumps(data))
        .time(data.get("published_timestamp", dt.datetime.utcnow().isoformat() + "Z"))
    )
    client.write(point)
    utils.set_field(data.get("filename"), "published_upload", 1)
    print(f"[publisher] published upload: {config.INFLUX_RECORDINGS_BUCKET}/{config.INFLUX_UPLOADS_TABLE}", path)

# Consumer loop for analysis results: dequeues and calls publish_analysis
def consume_analysis():
    while True:
        path = analysis_publish_queue.get()
        try:
            publish_analysis(path)
        finally:
            analysis_publish_queue.task_done()


# Consumer loop for upload links: dequeues and calls publish_upload
def consume_uploads():
    while True:
        path = uploads_publish_queue.get()
        try:
            publish_upload(path)
        finally:
            uploads_publish_queue.task_done()


def analysis_manual_pass(path: Path):
    print("[publisher] running analysis initial pass...")
    for wav in path.glob("*.json"):
        analysis_publish_queue.put(wav)
    print("[publisher] analysis initial pass done!")

def upload_manual_pass(path: Path):
    print("[publisher] upload running initial pass...")
    for wav in path.glob("*.json"):
        uploads_publish_queue.put(wav)
    print("[publisher] upload initial pass done!")


def main():
    # Start consumer threads
    Thread(target=consume_analysis, daemon=True).start()
    Thread(target=consume_uploads, daemon=True).start()

    observer = Observer()
    observer.schedule(AnalysisHandler(), str(config.ANALYSIS_DIR), recursive=False)
    observer.schedule(UploadHandler(), str(config.UPLOADS_DIR), recursive=False)
    observer.start()
    print("[publisher] watching analysis & uploads dirs…")

    analysis_manual_pass(Path(str(config.ANALYSIS_DIR)))
    upload_manual_pass(Path(str(config.UPLOADS_DIR)))
    try:
        while True:
            time.sleep(1)
    finally:
        observer.stop()
        observer.join()

if __name__ == "__main__":
    main()