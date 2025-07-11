import os
import time
import logging
from xmlrpc import client as xmlrpclib

from prometheus_client import start_http_server, Gauge, Counter
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
# Supervisord XML-RPC settings
# ──────────────────────────────────────────────────────────────────────────────
# Make sure you have [inet_http_server] enabled on 127.0.0.1:9001 in supervisord.conf
RPC_CLIENT = xmlrpclib.ServerProxy("http://127.0.0.1:9001/RPC2")

# Map supervisord statename → numeric code
STATE_MAP = {
    "RUNNING":  1,
    "STARTING": 2,
    "BACKOFF":  3,
    "STOPPED":  4,
    "EXITED":   5,
    "FATAL":    6,
    "UNKNOWN":  7,
}


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

supervisor_state_gauge = Gauge(
    "supervisor_process_state",
    "State of supervised process (1=RUNNING,2=STARTING,3=BACKOFF,4=STOPPED,5=EXITED,6=FATAL,7=UNKNOWN)",
    ["program"]
)

exit_code = Gauge(
    "supervisor_process_exitcode",
    "Last exit code of supervised process",
    ["program"]
)

# ────────────────────────────────────────────────────────────────────────
# Define Prometheus counters for processed jobs
# ────────────────────────────────────────────────────────────────────────
processed_analyze = Counter(
    "analyze_jobs_processed_total",
    "Total number of 'analyze' jobs fully processed"
)
processed_upload = Counter(
    "upload_jobs_processed_total",
    "Total number of 'upload' jobs fully processed"
)
processed_pub_analysis = Counter(
    "publish_analysis_jobs_processed_total",
    "Total number of 'publish_analysis' jobs fully processed"
)
processed_pub_upload = Counter(
    "publish_upload_jobs_processed_total",
    "Total number of 'publish_upload' jobs fully processed"
)

# Keep track of the last seen Redis counters
_last = {
    "analyze": 0,
    "upload": 0,
    "publish_analysis": 0,
    "publish_upload": 0,
}


def update_metrics():
    """Fetch current queue lengths & Supervisord states and update all Gauges."""

    # --- Redis queue lengths
    try:
        analyze_queue_gauge.set(r.xlen(ANALYZE_STREAM))
        upload_queue_gauge.set(r.xlen(UPLOAD_STREAM))
        publish_analysis_queue_gauge.set(r.xlen(PUBLISH_ANALYSIS_STREAM))
        publish_upload_queue_gauge.set(r.xlen(PUBLISH_UPLOAD_STREAM))
    except Exception as exc:
        log.error("Failed to update queue-length metrics: %s", exc)

    # fetch running totals from Redis
    try:
        for job_type, prom_counter in [
            ("analyze", processed_analyze),
            ("upload", processed_upload),
            ("publish_analysis", processed_pub_analysis),
            ("publish_upload", processed_pub_upload),
        ]:
            key = f"counter:processed:{job_type}"
            current = int(r.get(key) or 0)
            delta   = current - _last[job_type]
            if delta > 0:
                prom_counter.inc(delta)
                _last[job_type] = current
    except Exception as exc:
        log.error("Failed to update processed-jobs counters: %s", exc)

    
    # --- Supervisord process states
    try:
        procs = RPC_CLIENT.supervisor.getAllProcessInfo()
        for p in procs:
            name  = p["name"]
            state = p["statename"]
            code  = STATE_MAP.get(state, STATE_MAP["UNKNOWN"])
            supervisor_state_gauge.labels(program=name).set(code)
            if p['statename'] != 'RUNNING':
                exit_code.labels(program=name).set(p['exitstatus'])
    except Exception as exc:
        log.error("Failed to update Supervisor metrics: %s", exc)


def main():
    # Start the HTTP server to expose metrics.
    # Prometheus Agent will scrape this on localhost:8001/metrics
    start_http_server(8001)
    log.info("Metrics server listening on port 8001")

    # Periodic scrape loop
    interval = int(config.METRICS_INTERVAL_SEC)
    while True:
        update_metrics()
        time.sleep(interval)


if __name__ == "__main__":
    main()
