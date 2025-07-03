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

    tmp_path = config.RECORDINGS_TMP_DIR / filename
    final_path = config.RECORDINGS_DIR / filename

    with tmp_path.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    log.debug("Saved new recording to temporary file %s", tmp_path)

    tmp_path.rename(final_path)
    payload = {
        "filename": filename,
        "listener_id": listener_id,
        "local_path": str(final_path)
    }
    redis_utils.enqueue_job("stream:analyze", payload)
    redis_utils.enqueue_job("stream:upload",  payload)

    log.info("New recording %s", final_path)

    return {"status": "ok", "filename": filename}

log.info("Listening on http://localhost:8000%s", recording_endpoint)