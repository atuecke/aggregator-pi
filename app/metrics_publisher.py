import os
import time
from prometheus_client import start_http_server, Gauge
from . import config, utils


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()  # Initialise global logging once per process
import logging
log = logging.getLogger("app.metrics_publisher")
# ---------------------------------------------------------------------------

# Define one Gauge per metric
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
    """Fetch current queue lengths and update the Prometheus Gauges."""
    # Each call to get_pending_jobs returns a list of jobs in that state
    analyze_queue_gauge.set(len(utils.get_pending_jobs("analyze")))
    upload_queue_gauge.set(len(utils.get_pending_jobs("upload")))
    publish_analysis_queue_gauge.set(len(utils.get_pending_jobs("publish_analysis")))
    publish_upload_queue_gauge.set(len(utils.get_pending_jobs("publish_upload")))

def main():
    # Start the HTTP server to expose metrics.
    # Prometheus Agent will scrape this on localhost:8001/metrics
    start_http_server(8001)

    # Loop forever, updating metrics at the given interval
    while True:
        update_metrics()
        time.sleep(int(config.METRICS_INTERVAL_SEC))

if __name__ == "__main__":
    main()