"""HTTP upload receiver for aggregator.

Usage: POST /upload with multipart form-data `file=<audio>` and optional
`listener_id=<str>`. Saves file, then enqueues analyze & upload jobs.
"""
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from pathlib import Path
import shutil, datetime as dt

from . import config, utils, redis_utils


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()  # Initialise global logging once per process
import logging
log = logging.getLogger("app.receiver_http")
# ---------------------------------------------------------------------------

log.info("Setting up FastAPI server")
app = FastAPI(title="BirdNET Aggregator Receiver")
recording_endpoint = config.RECORDINGS_ENDPOINT

@app.post(recording_endpoint)
async def upload_audio(
    file: UploadFile = File(...),
    listener_id: str | None = Form(None),
):
    log.info("New audio recording received from %s: %s", listener_id, file.filename)
    if not file.filename.lower().endswith((".wav", ".flac", ".mp3")):
        raise HTTPException(400, "Unsupported file type")

    ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    base_name = Path(file.filename).name
    listener_id = listener_id or utils.get_listener_id_from_name(base_name)
    filename = f"{listener_id}_{ts}{Path(base_name).suffix}"

    recording_path = config.RECORDINGS_DIR / filename

    with recording_path.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    log.debug("Saved new recording to final path %s", recording_path)

    payload = {
        "filename": filename,
        "listener_id": listener_id,
        "local_path": str(recording_path)
    }
    
    if(config.ANALYZE_RECORDINGS):
        redis_utils.enqueue_job("stream:analyze", payload)
    if(config.UPLOAD_RAW_TO_CLOUD):
        redis_utils.enqueue_job("stream:upload",  payload)
    if(not config.ANALYZE_RECORDINGS and not config.UPLOAD_RAW_TO_CLOUD and config.DELETE_RECORDINGS):
        # mark both as analyzed so that it gets deleted
        redis_utils.enqueue_job("stream:done", {"filename": filename, "listener_id": listener_id, "stage": "uploaded"})
        redis_utils.enqueue_job("stream:done", {"filename": filename, "listener_id": listener_id, "stage": "analyzed"})


    log.info("New recording %s", recording_path)

    return {"status": "ok", "filename": filename}

log.info("Listening on http://localhost:8000%s", recording_endpoint)