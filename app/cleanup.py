import os, time, logging, json
from pathlib import Path
from . import utils, config

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()  # Initialise global logging once per process
import logging
log = logging.getLogger("app.cleanup")
# ---------------------------------------------------------------------------

SLEEP_SEC = int(os.getenv("CLEANUP_INTERVAL_SEC", "300"))  # 5 min default

REQUIRED_TYPES = (
    "analyze",
    "upload",
    "publish_analysis",
    "publish_upload",
)

def _delete_file(filename: str) -> bool:
    path: Path = config.RECORDINGS_DIR / filename
    if not path.exists():
        log.debug("File already absent: %s", path)
        return False
    try:
        path.unlink()
        log.info("Deleted raw recording %s", path)
        return True
    except Exception as exc:
        log.error("Failed to delete %s: %s", path, exc)
        return False

def cleanup_pass() -> None:
    log.info("Running cleanup pass")
    eligable_filenames = utils.get_eligible_filenames_for_deletion(REQUIRED_TYPES)
    for filename in eligable_filenames:
        deleted = _delete_file(filename)
        payload = {
            "deleted_at": utils._now(),
            "deleted": deleted,
        }
        listener_id = utils.get_listener_id_from_name(filename)
        # create a delete job → immediately mark done
        utils.create_job(filename, listener_id, "delete", payload)
        utils.set_job_status_by_filename_type(filename, "delete", "done", payload)

def main():
    log.info("Cleanup worker started (interval %ss)", SLEEP_SEC)
    while True:
        try:
            cleanup_pass()
        except Exception:
            log.exception("Cleanup pass failed")
        time.sleep(SLEEP_SEC)

if __name__ == "__main__":
    main()