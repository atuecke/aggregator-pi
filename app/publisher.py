"""Push analyzer & uploader JSON docs into InfluxDB 3.
Handler runs uploads/publish sequentially; blocking inside `on_created`."""

import json
import time
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

class AnalysisHandler(FileSystemEventHandler):
    def on_created(self, event):
        """Called by Watchdog when a JSON result appears."""
        if event.is_directory:
            return
        
        self.publish_analysis(Path(event.src_path))
    
    def publish_analysis(self, path: Path):
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

# event callback for when a new json is found
class UploadHandler(FileSystemEventHandler):
    def on_created(self, event):
        """Called by Watchdog when a JSON upload pointer appears."""
        if event.is_directory:
            return
        self.publish_upload(Path(event.src_path))

    def publish_upload(self, path: Path):
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


def analysis_initial_pass(analysisHandler: AnalysisHandler, path: Path):
    print("[publisher] running analysis initial pass...")
    for wav in path.glob("*.json"):
        analysisHandler.publish_analysis(wav)
    print("[publisher] analysis initial pass done!")

def upload_initial_pass(uploadHandler: UploadHandler, path: Path):
    print("[publisher] upload running initial pass...")
    for wav in path.glob("*.json"):
        uploadHandler.publish_upload(wav)
    print("[publisher] upload initial pass done!")


def main():
    observer = Observer()
    analysisHandler = AnalysisHandler()
    uploadHandler = UploadHandler()
    observer.schedule(analysisHandler, str(config.ANALYSIS_DIR), recursive=False)
    observer.schedule(uploadHandler, str(config.UPLOADS_DIR), recursive=False)
    observer.start()
    print("[publisher] watching analysis & uploads dirs…")

    analysis_initial_pass(analysisHandler, Path(str(config.ANALYSIS_DIR)))
    upload_initial_pass(uploadHandler, Path(str(config.UPLOADS_DIR)))
    try:
        while True:
            time.sleep(1)
    finally:
        observer.stop()
        observer.join()

if __name__ == "__main__":
    main()