"""Push analyzer & uploader JSON docs into InfluxDB 3.
Handler runs uploads/publish sequentially; blocking inside `on_created`."""

import json
import time
import queue
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from influxdb_client_3 import InfluxDBClient3, Point
from . import config, utils
import datetime as dt


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()  # Initialise global logging once per process
import logging
log = logging.getLogger("PUBLISHER")
# ---------------------------------------------------------------------------


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
    log.info("Published Analysis %s → %s", path, f"{config.INFLUX_RECORDINGS_BUCKET}/{config.INFLUX_ANALYSIS_TABLE}")


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
    log.info("Published Upload %s → %s", path, f"{config.INFLUX_RECORDINGS_BUCKET}/{config.INFLUX_UPLOADS_TABLE}")


def analysis_manual_pass(path: Path):
    log.info("Running manual pass for analysis publisher")
    
    # find all unpublished analysis results registered in the DB
    for analysis_path in utils.get_unpublished_analysis():
        # make sure the file actually exists
        if(analysis_path.exists()):
            # enqeue only if not already queued
            if analysis_path not in list(analysis_publish_queue.queue):
                log.info("Queueing unpublished analysis file: %s", analysis_path)
                analysis_publish_queue.put(analysis_path)
        else:
            log.warning("Missing file for analysis DB entry: %s", analysis_path)
    
    log.info("Analysis publisher manual pass complete")



def upload_manual_pass(path: Path):
    log.info("Running manual pass for upload publisher")

    # find all unpublished analysis results registered in the DB
    for upload_path in utils.get_unpublished_upload():
        # make sure the file actually exists
        if(upload_path.exists()):
            # enqeue only if not already queued
            if upload_path not in list(uploads_publish_queue.queue):
                log.info("Queueing unpublished upload file: %s", upload_path)
                uploads_publish_queue.put(upload_path)
        else:
            log.warning("Missing file for upload DB entry: %s", upload_path)

    log.info("Upload publisher manual pass complete")


def main():
    analysis_manual_pass(Path(str(config.ANALYSIS_DIR)))
    upload_manual_pass(Path(str(config.UPLOADS_DIR)))

    observer = Observer()
    observer.schedule(AnalysisHandler(), str(config.ANALYSIS_DIR), recursive=False)
    observer.schedule(UploadHandler(), str(config.UPLOADS_DIR), recursive=False)
    observer.start()
    log.info("Publisher watching %s and %s", str(config.ANALYSIS_DIR), str(config.UPLOADS_DIR))

    try:
        while True:
            if not analysis_publish_queue.empty():
                publish_analysis(analysis_publish_queue.get())
                analysis_publish_queue.task_done()
            if not uploads_publish_queue.empty():
                publish_upload(uploads_publish_queue.get())
                uploads_publish_queue.task_done()
            time.sleep(1)
    finally:
        observer.stop()
        observer.join()

if __name__ == "__main__":
    main()