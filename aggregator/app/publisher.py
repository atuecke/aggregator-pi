"""Pull analysis & upload payloads from Redis Streams and write to InfluxDB."""

import os
import time
import threading
import json
import datetime as dt
import logging

from influxdb_client import InfluxDBClient, Point, WriteOptions
from influxdb_client.client.write_api import ASYNCHRONOUS
from . import config, utils, redis_utils


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()
log = logging.getLogger("app.publisher")
# ---------------------------------------------------------------------------

# 1. Instantiate the InfluxDB client
client = InfluxDBClient(
    url=config.INFLUX_URL,
    token=config.INFLUX_TOKEN,
    org=config.INFLUX_ORG
)

# 2. Get the write API with batching options
write_api = client.write_api(
    write_options=WriteOptions(
        batch_size=50,          # send once 50 points are queued
        flush_interval=4_000,   # or every 4 s, whichever comes first
        jitter_interval=1_000,  # add up to 1 s random jitter to avoid thundering herd
        retry_interval=2_000,   # wait 2 s before retrying a failed batch
        max_retries=3,          # retry up to 3× before giving up
    )
)

# Redis stream & consumer-group settings
ANALYSIS_STREAM = "stream:publish_analysis"
UPLOAD_STREAM   = "stream:publish_upload"
ANALYSIS_GROUP  = "pub-analysis-group"
UPLOAD_GROUP    = "pub-upload-group"
CONSUMER        = "publisher-1"
BLOCK_MS        = 5000

# ensure groups exist (runs once)
def _init_groups():
    redis_utils.ensure_group(ANALYSIS_STREAM, ANALYSIS_GROUP)
    redis_utils.ensure_group(UPLOAD_STREAM,   UPLOAD_GROUP)


# thread for analysis → InfluxDB
def _analysis_publisher():
    log.info("Analysis publisher %s listening on %s", CONSUMER, ANALYSIS_STREAM)
    while True:
        item = redis_utils.claim_job(ANALYSIS_STREAM, ANALYSIS_GROUP, CONSUMER, block_ms=BLOCK_MS)
        if not item:
            continue
        msg_id, job = item
        filename = job.get("filename")

        log.debug("Publishing analysis for %s", filename)

        try:
            point = (
                Point(config.INFLUX_ANALYSIS_TABLE)
                .tag("filename", job.get("filename"))
                .tag("recorded_timestamp", job.get("recorded_timestamp"))
                .tag("aggregator_uuid", config.AGGREGATOR_UUID)
                .tag("listener_id", job.get("listener_id"))
                .tag("duration_sec", job.get("duration_sec"))
                .field("analyzed_timestamp", job.get("analyzed_timestamp"))
                .field("detections", json.dumps(job.get("detections")))
                .time(dt.datetime.utcnow().isoformat()+"Z")
            )
            # Write to the bucket
            write_api.write(bucket=config.INFLUX_RECORDINGS_BUCKET, record=point)
            log.info("Published analysis for %s", filename)
            redis_utils.ack_job(ANALYSIS_STREAM, ANALYSIS_GROUP, msg_id)

        except Exception as exc:
            log.error("Failed to publish analysis for %s: %s", filename, exc)
            # dead-letter
            dl = {**job, "error": str(exc), "failed_at": dt.datetime.utcnow().isoformat()+"Z"}
            redis_utils.enqueue_job(f"{ANALYSIS_STREAM}:dead", dl)
            redis_utils.ack_job(ANALYSIS_STREAM, ANALYSIS_GROUP, msg_id)


# thread for upload → InfluxDB
def _upload_publisher():
    log.info("Upload publisher %s listening on %s", CONSUMER, UPLOAD_STREAM)
    while True:
        item = redis_utils.claim_job(UPLOAD_STREAM, UPLOAD_GROUP, CONSUMER, block_ms=BLOCK_MS)
        if not item:
            continue
        msg_id, job = item
        filename = job.get("filename")

        log.debug("Publishing upload for %s", filename)

        try:
            point = (
                Point(config.INFLUX_UPLOADS_TABLE)
                .tag("filename", filename)
                .tag("recorded_timestamp", job.get("recorded_timestamp"))
                .tag("aggregator_uuid", config.AGGREGATOR_UUID)
                .tag("listener_id", job.get("listener_id"))
                .tag("duration_sec", job.get("duration_sec"))
                .field("remote", job.get("remote"))
                .field("uploaded_timestamp", job.get("uploaded_timestamp"))
                .time(dt.datetime.utcnow().isoformat()+"Z")
            )
            # Write to the bucket
            write_api.write(bucket=config.INFLUX_RECORDINGS_BUCKET, record=point)
            log.info("Published upload for %s", filename)
            redis_utils.ack_job(UPLOAD_STREAM, UPLOAD_GROUP, msg_id)

        except Exception as exc:
            log.error("Failed to publish upload for %s: %s", filename, exc)
            dl = {**job, "error": str(exc), "failed_at": dt.datetime.utcnow().isoformat()+"Z"}
            redis_utils.enqueue_job(f"{UPLOAD_STREAM}:dead", dl)
            redis_utils.ack_job(UPLOAD_STREAM, UPLOAD_GROUP, msg_id)


def main():
    # give Redis streams & groups a moment to initialize
    _init_groups()
    # start both publisher threads
    t1 = threading.Thread(target=_analysis_publisher, daemon=True)
    t2 = threading.Thread(target=_upload_publisher,   daemon=True)
    t1.start()
    t2.start()
    log.info("Publisher started, threads running.")
    # keep main alive
    t1.join()
    t2.join()

if __name__ == "__main__":
    main()