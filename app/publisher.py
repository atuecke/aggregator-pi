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
log = logging.getLogger("app.publisher")
# ---------------------------------------------------------------------------


# connect to InfluxDB3
client = InfluxDBClient3(
    host=config.INFLUX_URL,
    token=config.INFLUX_TOKEN,
    org=config.INFLUX_ORG,
    database=config.INFLUX_RECORDINGS_BUCKET,
)

def publish_analysis_jobs():
    log.debug("Polling for pending publish analysis jobs...")
    for job in utils.get_pending_jobs("publish_analysis"):
        filename = job["filename"]
        log.debug("New pending publish analysis job found for %s", filename)
        utils.set_job_status_by_filename_type(filename, "publish_analysis", "running")

        raw = job.get("payload")
        try:
            data = raw if isinstance(raw, dict) else json.loads(raw or "{}")
        except Exception:
            log.error("Invalid payload for analysis job %s: %s", filename, raw)
            utils.set_job_status_by_filename_type(filename, "publish_analysis", "error")
            continue
        
        point = (Point(config.INFLUX_ANALYSIS_TABLE)
                 .tag("filename", filename)
                 .tag("aggregator_uuid", config.AGGREGATOR_UUID)
                 .tag("listener_id", data.get("listener_id"))
                 .field("data", json.dumps(data))
                 .time(data.get("analyzed_timestamp")))
        client.write(point)

        meta = {
            "published_to": f"{config.INFLUX_RECORDINGS_BUCKET}/{config.INFLUX_ANALYSIS_TABLE}",
            "published_ts": f"{dt.datetime.utcnow().isoformat()}Z"
        }
        log.info("Published analysis for %s to %s", filename, meta["published_to"])
        utils.set_job_status_by_filename_type(filename, "publish_analysis", "done", meta)


def publish_upload_jobs():
    log.debug("Polling for pending publish upload jobs...")
    for job in utils.get_pending_jobs("publish_upload"):
        filename = job["filename"]
        log.debug("New pending publish upload job found for %s", filename)
        utils.set_job_status_by_filename_type(filename, "publish_upload", "running")

        raw = job.get("payload")
        try:
            data = raw if isinstance(raw, dict) else json.loads(raw or "{}")
        except Exception:
            log.error("Invalid payload for upload job %s: %s", job["id"], raw)
            utils.set_job_status_by_filename_type(filename, "publish_upload", "error")
            continue

        point = (Point(config.INFLUX_UPLOADS_TABLE)
                 .tag("filename", filename)
                 .tag("aggregator_uuid", config.AGGREGATOR_UUID)
                 .tag("listener_id", job.get("listener_id"))
                 .field("remote", data.get("remote"))
                 .field("uploaded_at", data.get("uploaded_at"))
                 .field("data", json.dumps(data))
                 .time(data.get("uploaded_at")))
        client.write(point)

        meta = {
            "published_to": f"{config.INFLUX_RECORDINGS_BUCKET}/{config.INFLUX_UPLOADS_TABLE}",
            "published_ts": f"{dt.datetime.utcnow().isoformat()}Z"
        }
        log.info("Published upload for %s at %s", filename, meta)
        utils.set_job_status_by_filename_type(filename, "publish_upload", "done", meta)


def main():
    log.info("Publisher DB poller running every %s's", config.PUBLISH_INTERVAL_SEC)
    while True:
        publish_analysis_jobs()
        publish_upload_jobs()
        time.sleep(config.PUBLISH_INTERVAL_SEC)


if __name__ == "__main__":
    main()