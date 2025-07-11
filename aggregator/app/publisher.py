"""Pull analysis & upload payloads from Redis Streams and write to InfluxDB."""

import os
import time
import threading
import json
import datetime as dt
import logging

from influxdb_client_3 import InfluxDBClient3, Point, WriteOptions, write_client_options
from . import config, utils, redis_utils


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()
log = logging.getLogger("app.publisher")
# ---------------------------------------------------------------------------

# 1. Define batch/write parameters
wo = WriteOptions(
    batch_size=50,          # send once 50 points are queued
    flush_interval=4_000,   # or every 4 s, whichever comes first
    jitter_interval=1_000,  # add up to 1 s random jitter to avoid thundering herd
    retry_interval=2_000,   # wait 2 s before retrying a failed batch
    max_retries=3,          # retry up to 3× before giving up
)

# Wrap it (and any callbacks) into a dict
wco = write_client_options(write_options=wo)

# 3. Instantiate the InfluxDB client with batching enabled
client = InfluxDBClient3(
    host=config.INFLUX_URL,
    token=config.INFLUX_TOKEN,
    org=config.INFLUX_ORG,
    database=config.INFLUX_RECORDINGS_BUCKET,
    write_client_options=wco,      # ← here’s the batching config
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
        payload  = job
        log.debug("Publishing analysis for %s", filename)

        try:
            point = (
                Point(config.INFLUX_ANALYSIS_TABLE)
                .tag("filename", filename)
                .tag("aggregator_uuid", config.AGGREGATOR_UUID)
                .tag("listener_id", payload.get("listener_id"))
                .field("data", json.dumps(payload))
                .time(payload.get("analyzed_timestamp"))
            )
            client.write(point)
            log.info("Published analysis for %s", filename)
            redis_utils.ack_job(ANALYSIS_STREAM, ANALYSIS_GROUP, msg_id)

        except Exception as exc:
            log.error("Failed to publish analysis for %s: %s", filename, exc)
            # dead-letter
            dl = {**payload, "error": str(exc), "failed_at": dt.datetime.utcnow().isoformat()+"Z"}
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
        payload  = job
        log.debug("Publishing upload for %s", filename)

        try:
            point = (
                Point(config.INFLUX_UPLOADS_TABLE)
                .tag("filename", filename)
                .tag("aggregator_uuid", config.AGGREGATOR_UUID)
                .tag("listener_id", payload.get("listener_id"))
                .field("remote", payload.get("remote"))
                .field("uploaded_at", payload.get("uploaded_at"))
                .field("data", json.dumps(payload))
                .time(dt.datetime.utcnow().isoformat() + "Z")
            )
            client.write(point)
            log.info("Published upload for %s", filename)
            redis_utils.ack_job(UPLOAD_STREAM, UPLOAD_GROUP, msg_id)

        except Exception as exc:
            log.error("Failed to publish upload for %s: %s", filename, exc)
            dl = {**payload, "error": str(exc), "failed_at": dt.datetime.utcnow().isoformat()+"Z"}
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