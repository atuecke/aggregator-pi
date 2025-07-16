"""
Streaming-receiver for 32 KiB audio pages sent by an ESP32 over HTTP-chunked
transfer.  Each page is wrapped in a **5-byte frame header**:

    Byte 0-2  little-endian sequence-number  (uint24, wraps at 16 777 216)
    Byte 3-4  little-endian payload-length   (uint16, 0x8000 for audio pages)

Design rules
------------
* A *RIFF/WAV header* arrives as frame #0 (length = 44 B) after every
  duty-cycle rollover or 4.3 GB limit; we treat that as "start new file".
* Missing ≤ ``MAX_SILENT_PAGES`` (default 9 ≈ 3 s at 48 k Hz) ⇒ pad with
  zero-filled pages so BirdNET windows stay aligned.
* Missing  > ``MAX_SILENT_PAGES`` ⇒ close current WAV, start a fresh one.
* On TCP disconnect we finalise the current file, move it atomically into
  ``/data/recordings/`` and enqueue *analyze* / *upload* Redis jobs.
* Files shorter than MIN_RECORDING_DURATION_SEC are either deleted or
  concatenated with the next file depending on CONCAT_SHORT_RECORDINGS setting.
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
MAX_SILENT_PAGES    = 9               # ≈ 3 s gap

# New configuration options
MIN_RECORDING_DURATION_SEC = 3        # Minimum recording length in seconds
CONCAT_SHORT_RECORDINGS = True        # If True, concatenate short recordings; if False, delete them

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
        self.bytes_written: int = 0
        self.last_header: bytes | None = None
        self.max_temp_bytes = config.MAX_RECORDING_BYTES
        
        # Audio format info for duration calculation
        self.sample_rate: int = 48000  # default
        self.num_channels: int = 1     # default
        self.bits_per_sample: int = 16 # default
        self.bytes_per_second: int = 96000  # default
        
        # For handling short recordings
        self.pending_short_file: Optional[Path] = None
        self.pending_short_duration: float = 0.0
        self.pending_short_bytes: int = 0  # Track bytes in pending file
        self.pending_start_ts: Optional[str] = None  # Original timestamp
        self.gap_induced_rollover: bool = False  # Track if rollover was due to gap
        
        # Gap tracking
        self.num_gaps: int = 0  # Count of gaps/missing pages in current recording
        self.pending_num_gaps: int = 0  # Gaps in pending file

    # -- file lifecycle ------------------------------------------------------
    def _open_new(self, first_payload: bytes):
        """Start a new WAV file (first_payload is the 44-byte RIFF header)."""
        # Check if we have a pending short file to concatenate
        if self.pending_short_file and CONCAT_SHORT_RECORDINGS:
            log.info("Concatenating previous short recording (%0.2fs) with new stream", 
                     self.pending_short_duration)
            # We'll append to the existing file instead of creating a new one
            self.tmp_path = self.pending_short_file
            self.tmp_fh = self.tmp_path.open("ab")
            # Restore state from pending file
            self.start_ts = self.pending_start_ts  # Keep original timestamp
            self.bytes_written = self.pending_short_bytes  # Continue from previous byte count
            self.num_gaps = self.pending_num_gaps  # Carry over gap count
            # Don't write the new header - we're continuing the previous file
            # Just update expected_seq and continue
            self.expected_seq = 1
            self.pending_short_file = None
            self.pending_short_duration = 0.0
            self.pending_short_bytes = 0
            self.pending_start_ts = None
            self.pending_num_gaps = 0
            # Restore the audio format from the new header
            self._parse_wav_header(first_payload)
            return
        
        self.start_ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        filename = f"{self.listener_id}_{self.start_ts}.wav"
        self.tmp_path = config.RECORDINGS_TMP_DIR / (filename + ".tmp")
        self.tmp_fh = self.tmp_path.open("wb")
        self.tmp_fh.write(first_payload)
        self.expected_seq = 1  # first data page must be seq 1
        self.last_header = first_payload # save header for forced rollovers
        self.bytes_written = len(first_payload)
        self.num_gaps = 0  # Reset gap counter for new file
        log.debug("Opened new WAV %s", self.tmp_path)

        # Parse WAV header
        self._parse_wav_header(first_payload)

    def _parse_wav_header(self, header_bytes: bytes):
        """Parse WAV header to compute bytes/sec & max-bytes"""
        try:
            # WAV fmt: channels @offset 22 (2B), samplerate @24 (4B), bits/sample @34 (2B)
            self.num_channels = struct.unpack_from("<H", header_bytes, 22)[0]
            self.sample_rate = struct.unpack_from("<I", header_bytes, 24)[0]
            self.bits_per_sample = struct.unpack_from("<H", header_bytes, 34)[0]
            self.bytes_per_second = (self.bits_per_sample // 8) * self.num_channels * self.sample_rate
            self.max_temp_bytes = self.bytes_per_second * config.MAX_RECORDING_DURATION_SEC
            log.debug(
                "Header parsed: %d ch @%d Hz, %d-bit ⇒ %dB/s → max %dB",
                self.num_channels, self.sample_rate, self.bits_per_sample, 
                self.bytes_per_second, self.max_temp_bytes
            )
        except Exception:
            # fallback: use defaults
            self.bytes_per_second = 96000  # 48kHz, 16-bit, mono
            self.max_temp_bytes = config.MAX_RECORDING_BYTES
            log.warning("Failed to parse WAV header; using defaults: %dB/s", self.bytes_per_second)

    def _calculate_duration(self, file_path: Path) -> float:
        """Calculate duration of WAV file in seconds"""
        try:
            file_size = file_path.stat().st_size
            # Subtract header size (typically 44 bytes, but could be more with metadata)
            # Read the data chunk size instead for accuracy
            with file_path.open("rb") as f:
                f.seek(0)
                header = f.read(8192)
                idx = header.find(b"data")
                if idx >= 0:
                    f.seek(idx + 4)
                    data_size = struct.unpack("<I", f.read(4))[0]
                    duration = data_size / self.bytes_per_second
                    return duration
                else:
                    # Fallback: estimate based on file size
                    audio_bytes = file_size - 44  # assume standard header
                    return audio_bytes / self.bytes_per_second
        except Exception as e:
            log.warning("Error calculating duration: %s", e)
            return 0.0

    def _finalise(self):
        """Close tmp file, check duration, and either save, delete, or queue for concatenation."""
        if not self.tmp_fh:
            return
        self.tmp_fh.flush()
        self.tmp_fh.close()

        path = self.tmp_path
        total = path.stat().st_size
        riff_sz = total - 8

        # Patch RIFF and data chunk sizes
        with path.open("r+b") as f:
            # Patch RIFF size at byte 4
            f.seek(4)
            f.write(struct.pack("<I", riff_sz))

            # Scan header for 'data' label
            f.seek(0)
            header_bytes = f.read(8192)
            idx = header_bytes.find(b"data")
            if idx < 0:
                log.error("WAV header: no 'data' chunk found; skipping data-size patch")
            else:
                data_size = total - (idx + 8)
                f.seek(idx + 4)
                f.write(struct.pack("<I", data_size))

            f.flush()
            os.fsync(f.fileno())

        # Calculate duration
        duration = self._calculate_duration(path)
        log.debug("Recording duration: %.2f seconds, gaps: %d", duration, self.num_gaps)

        # Check if recording meets minimum duration
        if duration < MIN_RECORDING_DURATION_SEC:
            if self.gap_induced_rollover or not CONCAT_SHORT_RECORDINGS:
                # Delete short recordings if gap-induced or concatenation disabled
                log.warning("Recording too short (%.2fs < %ds)%s - deleting %s", 
                           duration, MIN_RECORDING_DURATION_SEC,
                           " due to gap-induced rollover" if self.gap_induced_rollover else "",
                           path)
                path.unlink()
            else:
                # Queue for concatenation with next recording
                log.info("Recording too short (%.2fs < %ds) - queuing for concatenation", 
                         duration, MIN_RECORDING_DURATION_SEC)
                self.pending_short_file = path
                self.pending_short_duration = duration
                self.pending_short_bytes = self.bytes_written
                self.pending_start_ts = self.start_ts
                self.pending_num_gaps = self.num_gaps
        else:
            # Recording is long enough - save it
            final_path = config.RECORDINGS_DIR / self.tmp_path.name[:-4]  # strip .tmp
            shutil.move(self.tmp_path, final_path)
            log.info("Finalised recording %s (%.2fs, %d gaps)", final_path, duration, self.num_gaps)

            payload = {
                "filename": final_path.name,
                "recorded_timestamp": self.start_ts,
                "listener_id": self.listener_id,
                "local_path": str(final_path),
                "num_gaps": self.num_gaps
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
        self.bytes_written = 0
        self.gap_induced_rollover = False
        self.num_gaps = 0

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
            missing = (seq - self.expected_seq) % (1 << 24)
            if 0 < missing <= MAX_SILENT_PAGES:
                log.warning("Missing %d pages - padding silence", missing)
                self.tmp_fh.write(b"\x00" * PAGE_BYTES * missing)
                self.bytes_written += PAGE_BYTES * missing  # Track padded bytes
                self.num_gaps += 1  # Increment gap counter
            else:
                log.warning("Gap %d pages > threshold; rolling file", missing)
                self.gap_induced_rollover = True  # Mark as gap-induced
                self._finalise()
                # cannot write payload until new header arrives
                return

        # write page & bump expected seq
        self.tmp_fh.write(data)
        self.expected_seq = (seq + 1) % (1 << 24)
        self.bytes_written += len(data)

        # Forced rollover if we exceed max_temp_bytes
        if self.max_temp_bytes is not None and self.bytes_written > self.max_temp_bytes:
            log.debug("Temp file grew to %d bytes > %d; rolling over",
                        self.bytes_written, self.max_temp_bytes)
            self._finalise()
            # immediately start a new file with the same header
            assert self.last_header is not None, "No header to reopen with!"
            self._open_new(self.last_header)
            # Since this is a size-based rollover (not gap-based), the expected_seq continues
            self.expected_seq = (seq + 1) % (1 << 24)

    def cleanup_pending_files(self):
        """Clean up any pending short files on stream end"""
        if self.pending_short_file:
            log.warning("Stream ended with pending short file (%.2fs) - deleting %s",
                       self.pending_short_duration, self.pending_short_file)
            self.pending_short_file.unlink()
            self.pending_short_file = None
            self.pending_short_duration = 0.0
            self.pending_short_bytes = 0
            self.pending_start_ts = None
            self.pending_num_gaps = 0

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
        # Clean up any pending short files
        assembler.cleanup_pending_files()
        return JSONResponse({"status": "ok", "listener": listener_id})

    except Exception as exc:
        log.exception("Stream from %s aborted: %s", listener_id, exc)
        assembler._finalise()
        assembler.cleanup_pending_files()
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
    filename = f"{listener_id}_{ts}{Path(file.filename).suffix}"

    recording_path = config.RECORDINGS_DIR / filename

    with recording_path.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    log.debug("Saved new recording to final path %s", recording_path)

    payload = {
        "filename": filename,
        "listener_id": listener_id,
        "local_path": str(recording_path),
        "num_gaps": 0
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