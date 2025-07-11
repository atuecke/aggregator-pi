"""
Streaming-receiver for 32 KiB audio pages sent by an ESP32 over HTTP-chunked
transfer.  Each page is wrapped in a **5-byte frame header**:

    Byte 0-2  little-endian sequence-number  (uint24, wraps at 16 777 216)
    Byte 3-4  little-endian payload-length   (uint16, 0x8000 for audio pages)

Design rules
------------
* A *RIFF/WAV header* arrives as frame #0 (length = 44 B) after every
  duty-cycle rollover or 4.3 GB limit; we treat that as “start new file”.
* Missing ≤ ``MAX_SILENT_PAGES`` (default 9 ≈ 3 s at 48 k Hz) ⇒ pad with
  zero-filled pages so BirdNET windows stay aligned.
* Missing  > ``MAX_SILENT_PAGES`` ⇒ close current WAV, start a fresh one.
* On TCP disconnect we finalise the current file, move it atomically into
  ``/data/recordings/`` and enqueue *analyze* / *upload* Redis jobs.
"""

from __future__ import annotations
import asyncio, datetime as dt, io, os, shutil, struct, time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import JSONResponse

from . import config, utils, redis_utils

# --------------------------------------------------------------------------- #
# Constants - tweak via env-vars / YAML                                       #
# --------------------------------------------------------------------------- #
PAGE_BYTES          = 32_768          # 32 KiB page written by AudioMoth
FRAME_HEADER_BYTES  = 5               # uint24 seq  +  uint16 len
MAX_SILENT_PAGES    = 9  # ≈ 3 s gap

# --------------------------------------------------------------------------- #
utils.setup_logging()
log = utils.logging.getLogger("app.receiver_http")
# --------------------------------------------------------------------------- #

app = FastAPI(title="BirdNET Aggregator Recording Receiver")


recordings_stream_endpoint = config.RECORDINGS_STREAM_ENDPOINT
recording_upload_endpoint = config.RECORDINGS_UPLOAD_ENDPOINT

# ─────────────────────────────────────────────────────────────────────────────
# Helper: assemble & manage a single WAV on disk
# ─────────────────────────────────────────────────────────────────────────────
class WaveAssembler:
    """Accumulates frames into ``*.wav.tmp`` then moves to live directory."""

    def __init__(self, listener_id: str):
        self.listener_id = listener_id
        self.expected_seq: Optional[int] = None
        self.tmp_fh: Optional[io.BufferedWriter] = None
        self.tmp_path: Optional[Path] = None
        self.start_ts: Optional[str] = None  # UTC timestamp string

    # -- file lifecycle ------------------------------------------------------
    def _open_new(self, first_payload: bytes):
        """Start a new WAV file (first_payload is the 44-byte RIFF header)."""
        self.start_ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        filename = f"{self.listener_id}_{self.start_ts}.wav"
        self.tmp_path = config.RECORDINGS_TMP_DIR / (filename + ".tmp")
        self.tmp_fh = self.tmp_path.open("wb")
        self.tmp_fh.write(first_payload)
        self.expected_seq = 1  # first data page must be seq 1
        log.debug("Opened new WAV %s", self.tmp_path)

    def _finalise(self):
        """Close tmp file, move into recordings dir, enqueue jobs."""
        if not self.tmp_fh:
            return
        self.tmp_fh.flush()
        self.tmp_fh.close()

        final_path = config.RECORDINGS_DIR / self.tmp_path.name[:-4]  # strip .tmp
        shutil.move(self.tmp_path, final_path)
        log.info("Finalised recording %s", final_path)

        payload = {
            "filename": final_path.name,
            "listener_id": self.listener_id,
            "local_path": str(final_path)
        }
        # enqueue downstream jobs
        if config.ANALYZE_RECORDINGS:
            redis_utils.enqueue_job("stream:analyze", payload)
        if config.UPLOAD_RAW_TO_CLOUD:
            redis_utils.enqueue_job("stream:upload",  payload)
        if (not config.ANALYZE_RECORDINGS and
                not config.UPLOAD_RAW_TO_CLOUD and
                config.DELETE_RECORDINGS):
            # immediately eligible for deletion
            redis_utils.enqueue_job("stream:done", {**payload, "stage": "analyzed"})
            redis_utils.enqueue_job("stream:done", {**payload, "stage": "uploaded"})

        # reset state
        self.tmp_fh = None
        self.tmp_path = None
        self.expected_seq = None
        self.start_ts = None

    # -- frame ingestion -----------------------------------------------------
    def ingest_frame(self, seq: int, data: bytes):
        """Process one framed payload."""
        # metadata frame (seq == 0xFFFFFF) – skip for now
        if seq == 0xFFFFFF:
            log.debug("Metadata frame (%d B) skipped", len(data))
            return
        

        # new-header frame?
        if seq == 0:
            # close any previous file
            self._finalise()
            self._open_new(data)
            return
        
        # Header sniffing – does payload *start* with RIFF/WAVE?
        if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WAVE":
            log.warning("RIFF header detected mid-stream; rolling file "
                        "(seq=%d, expected=%s)", seq, self.expected_seq)
            self._finalise()
            self._open_new(data)
            # counter stays as-is -> next expected seq is seq+1
            self.expected_seq = (seq + 1) % (1 << 24)
            return

        if self.tmp_fh is None:
            log.warning("Data frame before header - dropping %d B", len(data))
            return

        # gap detection
        if self.expected_seq is not None and seq != self.expected_seq:
            log.debug("seq=%d, expected_seq=%d", seq, self.expected_seq)
            missing = (seq - self.expected_seq) % (1 << 24)
            if 0 < missing <= MAX_SILENT_PAGES:
                log.warning("Missing %d pages - padding silence", missing)
                self.tmp_fh.write(b"\x00" * PAGE_BYTES * missing)
            else:
                log.warning("Gap %d pages > threshold; rolling file", missing)
                self._finalise()
                # cannot write payload until new header arrives
                return

        # write page & bump expected seq
        self.tmp_fh.write(data)
        self.expected_seq = (seq + 1) % (1 << 24)


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI endpoint
# ─────────────────────────────────────────────────────────────────────────────
@app.post(recordings_stream_endpoint)
async def receive_stream(
    request: Request,
    listener_id: str = Query(..., description="Unique ID of recording node"),
):
    """
    Consume the chunked HTTP body in real-time, frame-parse 5-byte headers,
    and stream pages to disk via :class:`WaveAssembler`.
    """
    log.info("New stream from %s", listener_id)
    assembler = WaveAssembler(listener_id)
    buffer = bytearray()

    try:
        async for chunk in request.stream():
            buffer.extend(chunk)

            # parse as many complete frames as are available
            while len(buffer) >= FRAME_HEADER_BYTES:
                seq  = int.from_bytes(buffer[0:3], "little")
                plen = struct.unpack_from("<H", buffer, 3)[0]

                if len(buffer) < FRAME_HEADER_BYTES + plen:
                    break  # incomplete – wait for more bytes

                payload = bytes(buffer[5:5 + plen])
                del buffer[:5 + plen]  # pop from buffer

                assembler.ingest_frame(seq, payload)

        # client closed connection → finalise
        assembler._finalise()
        return JSONResponse({"status": "ok", "listener": listener_id})

    except Exception as exc:
        log.exception("Stream from %s aborted: %s", listener_id, exc)
        assembler._finalise()
        raise HTTPException(500, f"Receiver error: {exc}") from exc


log.info("Receiver-stream endpoint mounted on http://localhost:8000%s", recordings_stream_endpoint)

@app.post(recording_upload_endpoint)
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

log.info("Legacy upload enpoint mounted on http://localhost:8000%s", recording_upload_endpoint)