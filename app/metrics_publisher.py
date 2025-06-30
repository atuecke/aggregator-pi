"""Every 30's capture CPU / RAM / disk / net stats and stream to InfluxDB'3."""

import time, datetime as dt, psutil, json
from pathlib import Path
from influxdb_client_3 import InfluxDBClient3, Point
from . import config, utils

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()  # Initialise global logging once per process
import logging
log = logging.getLogger("app.metrics_publisher")
# ---------------------------------------------------------------------------

_last_io = None

client = InfluxDBClient3(
    host=config.INFLUX_URL,
    token=config.INFLUX_TOKEN,
    org=config.INFLUX_ORG,
    database=config.INFLUX_METRICS_BUCKET
)

METRICS_LOG_PATH = Path(config.METRICS_LOG_PATH)

def push_metrics():
    log.debug("Gathering system metrics...")

    cpu  = psutil.cpu_percent()
    per_core = psutil.cpu_percent(percpu=True)

    mem  = psutil.virtual_memory().percent
    disk = psutil.disk_usage(str(config.BASE_DIR)).percent

    analyze_queue = len(utils.get_pending_jobs("analyze"))
    upload_queue = len(utils.get_pending_jobs("upload"))
    publish_analysis_queue = len(utils.get_pending_jobs("publish_analysis"))
    publish_upload_queue = len(utils.get_pending_jobs("publish_upload"))

    global _last_io
    net = psutil.net_io_counters()
    if _last_io is None:
        # first pass — no delta to report (or you could treat total as delta)
        delta_sent = net.bytes_sent
        delta_recv = net.bytes_recv
    else:
        delta_sent = net.bytes_sent - _last_io.bytes_sent
        delta_recv = net.bytes_recv - _last_io.bytes_recv
     # update for next time
    _last_io = net

    ts   = dt.datetime.utcnow().isoformat() + "Z"
    
    
    # also put the state of all of the scripts
    point = (
        Point("pi_metrics")
        .tag("aggregator_uuid", config.AGGREGATOR_UUID)
        .field("cpu (%)", cpu)
        .field("cpu cores (%)", json.dumps(per_core))
        .field("mem (%)", mem)
        .field("disk (%)", disk)
        .field("bytes_sent", delta_sent)
        .field("bytes_recv", delta_recv)
        .field("analyze_queue", analyze_queue)
        .field("upload_queue", upload_queue)
        .field("publish_analysis_queue", publish_analysis_queue)
        .field("publish_upload_queue", publish_upload_queue)
        .time(ts)
    )
    client.write(point)
    METRICS_LOG_PATH.write_text(json.dumps(point.to_line_protocol()) + "\n")
    log.info("Metrics pushed at %s", ts)
        

if __name__ == "__main__":
    log.info("Publishing system metrics every %s seconds", config.METRICS_INTERVAL_SEC)
    while(True):
        time.sleep(int(config.METRICS_INTERVAL_SEC))
        push_metrics()