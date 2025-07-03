import os
import time
import logging

from prometheus_client import start_http_server, Gauge
from . import config, utils
import redis


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()  # Initialise global logging once per process
log = logging.getLogger("app.metrics_publisher")
# ---------------------------------------------------------------------------

# ──────────────────────────────────────────────────────────────────────────────
# Redis connection & stream names
# ──────────────────────────────────────────────────────────────────────────────
REDIS_URL     = os.getenv("REDIS_URL", "redis://localhost:6379")
r             = redis.Redis.from_url(REDIS_URL, decode_responses=True)

ANALYZE_STREAM         = "stream:analyze"
UPLOAD_STREAM          = "stream:upload"
PUBLISH_ANALYSIS_STREAM= "stream:publish_analysis"
PUBLISH_UPLOAD_STREAM  = "stream:publish_upload"


# ──────────────────────────────────────────────────────────────────────────────
# Define one Gauge per metric
# ──────────────────────────────────────────────────────────────────────────────
analyze_queue_gauge = Gauge(
    "analyze_queue_length",
    "Number of pending 'analyze' jobs in the aggregator"
)
upload_queue_gauge = Gauge(
    "upload_queue_length",
    "Number of pending 'upload' jobs in the aggregator"
)
publish_analysis_queue_gauge = Gauge(
    "publish_analysis_queue_length",
    "Number of pending 'publish_analysis' jobs in the aggregator"
)
publish_upload_queue_gauge = Gauge(
    "publish_upload_queue_length",
    "Number of pending 'publish_upload' jobs in the aggregator"
)


def update_metrics():
    """Fetch current queue lengths from Redis and update Prometheus Gauges."""
    try:
        analyze_queue_gauge.set(r.xlen(ANALYZE_STREAM))
        upload_queue_gauge.set(r.xlen(UPLOAD_STREAM))
        publish_analysis_queue_gauge.set(r.xlen(PUBLISH_ANALYSIS_STREAM))
        publish_upload_queue_gauge.set(r.xlen(PUBLISH_UPLOAD_STREAM))
    except Exception as exc:
        log.error("Failed to update queue-length metrics: %s", exc)


def main():
    # Start the HTTP server to expose metrics.
    # Prometheus Agent will scrape this on localhost:8001/metrics
    start_http_server(8001)
    log.info("Metrics server listening on port 8001")

    # Loop forever, updating metrics at the given interval
    while True:
        update_metrics()
        time.sleep(int(config.METRICS_INTERVAL_SEC))


if __name__ == "__main__":
    main()
