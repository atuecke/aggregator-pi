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


client = InfluxDBClient3(
    host=config.INFLUX_URL,
    token=config.INFLUX_TOKEN,
    org=config.INFLUX_ORG,
    database=config.INFLUX_METRICS_BUCKET
)

LOG_PATH = Path(config.LOG_PATH)

def push_metrics():
    while True:
        log.debug("Gathering system metrics...")
        ts   = dt.datetime.utcnow()
        cpu  = psutil.cpu_percent()
        mem  = psutil.virtual_memory().percent
        disk = psutil.disk_usage(str(config.BASE_DIR)).percent
        net  = psutil.net_io_counters()
# also put the state of all of the scripts
        point = (
            Point("pi_metrics")
            .field("cpu", cpu)
            .field("mem", mem)
            .field("disk", disk)
            .field("bytes_sent", net.bytes_sent)
            .field("bytes_recv", net.bytes_recv)
            .time(ts)
        )
        client.write(point)
        LOG_PATH.write_text(json.dumps(point.to_line_protocol()))
        log.info("Metrics pushed at %s", ts.isoformat())
        time.sleep(int(config.METRICS_INTERVAL_SEC))

if __name__ == "__main__":
    push_metrics()