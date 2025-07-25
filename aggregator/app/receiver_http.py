"""
Streaming-receiver for 32 KiB audio pages sent by an ESP32 over HTTP-chunked
transfer.  Each page is wrapped in a **6-byte frame header**:

    Byte 0-2  little-endian sequence-number  (uint24, wraps at 16 777 216)
    Byte 3-5  little-endian payload-length   (uint24, up to 16 777 215 bytes)

Design rules
------------
* A *RIFF/WAV header* arrives as frame #0 (length = 44 B) after every
  duty-cycle rollover or 4.3 GB limit; we treat that as "start new file".
  It can also arrive at the start of any frame (including 0) that has audio data proceeding it
* Missing ≤ ``MAX_SILENT_PAGES`` (default 9 ≈ 3 s at 48 k Hz) ⇒ pad with
  zero-filled pages so BirdNET windows stay aligned.
* Missing  > ``MAX_SILENT_PAGES`` ⇒ close current WAV, start a fresh one.
* On TCP disconnect we finalise the current file, move it atomically into
  ``/data/recordings/`` and enqueue *analyze* / *upload* Redis jobs.
* Files shorter than MIN_RECORDING_DURATION_SEC are deleted.
* Gap count is tracked and included in Redis payloads for quality monitoring.
"""

from __future__ import annotations
import asyncio, datetime as dt, io, os, shutil, struct, time
from pathlib import Path
from typing import Optional, Tuple

from fastapi import FastAPI, Request, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import JSONResponse

from . import config, utils, redis_utils

# --------------------------------------------------------------------------- #
# Constants - tweak via env-vars / YAML                                       #
# --------------------------------------------------------------------------- #
PAGE_BYTES          = 32_768          # 32 KiB page written by AudioMoth
FRAME_HEADER_BYTES  = 6               # uint24 seq  +  uint24 len
MAX_SILENT_PAGES    = 9               # ≈ 3 s gap
METADATA_SEQ        = 0xFFFFFF        # Special sequence number for metadata frames
MAX_SEQ             = 0xFFFFFE        # Maximum valid sequence number (before metadata)

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
        self.seen_first_header = False  # Track if we've seen the initial header
        
        # Audio format info for duration calculation
        self.sample_rate: int = 48000  # default
        self.num_channels: int = 1     # default
        self.bits_per_sample: int = 16 # default
        self.bytes_per_second: int = 96000  # default
        
        # Gap tracking
        self.num_gaps: int = 0  # Count of gaps/missing pages in current recording

    def _extract_wav_header_and_body(self, payload: bytes) -> Tuple[Optional[bytes], bytes]:
        """
        Extract WAV header and remaining audio data from a payload.
        
        Returns:
            Tuple of (header_bytes, audio_data)
            - header_bytes: The WAV header if found at start of payload, None otherwise
            - audio_data: The remaining audio data after the header (or full payload if no header)
        """
        # Check if payload starts with RIFF/WAVE header
        if len(payload) >= 12 and payload[0:4] == b"RIFF" and payload[8:12] == b"WAVE":
            # Try to locate the 'data' chunk
            search_span = payload[:8192]  # Search in first 8KB
            idx = search_span.find(b"data")
            
            if idx >= 0 and len(payload) >= idx + 8:  # Found 'data' + size field
                header_end = idx + 8
                header_bytes = payload[:header_end]
                audio_data = payload[header_end:]
                log.debug("Extracted WAV header (%d bytes) from payload", len(header_bytes))
                return header_bytes, audio_data
        
        # No header found
        return None, payload

    def _build_default_wav_header(self) -> bytes:
        """
        Build a default PCM WAV header from configuration settings.
        
        Returns:
            bytes: A minimal 44-byte WAV header
        """
        ch = config.AUDIO_NUM_CHANNELS
        sr = config.AUDIO_SAMPLE_RATE
        bps = config.AUDIO_BITS_PER_SAMPLE
        br = sr * ch * bps // 8  # byte-rate
        align = ch * bps // 8

        header = (
            b"RIFF" + b"\x00\x00\x00\x00"          # RIFF size (patched later)
            + b"WAVEfmt " + (16).to_bytes(4, "little")
            + (1).to_bytes(2, "little")            # PCM format
            + ch.to_bytes(2, "little")             # Channels
            + sr.to_bytes(4, "little")             # Sample rate
            + br.to_bytes(4, "little")             # Byte rate
            + align.to_bytes(2, "little")          # Block align
            + bps.to_bytes(2, "little")            # Bits per sample
            + b"data" + b"\x00\x00\x00\x00"        # data size (patched later)
        )
        
        log.debug("Built default WAV header: %d ch, %d Hz, %d-bit", ch, sr, bps)
        return header

    def _parse_wav_header(self, header_bytes: bytes):
        """
        Parse WAV header to extract audio format parameters.
        Updates instance variables for sample rate, channels, etc.
        """
        try:
            # WAV fmt chunk locations:
            # - channels: offset 22 (2 bytes)
            # - sample rate: offset 24 (4 bytes)  
            # - bits per sample: offset 34 (2 bytes)
            self.num_channels = struct.unpack_from("<H", header_bytes, 22)[0]
            self.sample_rate = struct.unpack_from("<I", header_bytes, 24)[0]
            self.bits_per_sample = struct.unpack_from("<H", header_bytes, 34)[0]
            self.bytes_per_second = (self.bits_per_sample // 8) * self.num_channels * self.sample_rate
            self.max_temp_bytes = self.bytes_per_second * config.MAX_RECORDING_DURATION_SEC
            
            log.debug(
                "Parsed header: %d ch @ %d Hz, %d-bit => %d B/s, max %d B",
                self.num_channels, self.sample_rate, self.bits_per_sample, 
                self.bytes_per_second, self.max_temp_bytes
            )
        except Exception as e:
            # Fallback to defaults if parsing fails
            self.bytes_per_second = 96000  # 48kHz, 16-bit, mono
            self.max_temp_bytes = config.MAX_RECORDING_BYTES
            log.warning("Failed to parse WAV header (%s), using defaults: %d B/s", e, self.bytes_per_second)

    def _open_new(self, header_in_payload: bytes, audio_data: bytes):
        """
        Start a new WAV file with the given payload.
        
        Args:
            first_payload: Initial data which may contain header and/or audio data
        """
        
        # Determine which header to use
        if header_in_payload:
            # Use the header from the payload
            wav_header = header_in_payload
            self.last_header = header_in_payload  # Cache for future forced rollovers
        elif self.last_header:
            # Use cached header from previous file
            wav_header = self.last_header
            log.debug("Used last saved wave header for new file")
        else:
            # Build default header
            wav_header = self._build_default_wav_header()
            self.last_header = wav_header
            log.debug("Used last default wave header for new file")
        
        # Parse header to update audio format info
        self._parse_wav_header(wav_header)
        
        # Create new file
        self.start_ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        filename = f"{self.listener_id}_{self.start_ts}.wav"
        self.tmp_path = config.RECORDINGS_TMP_DIR / (filename + ".tmp")
        self.tmp_fh = self.tmp_path.open("wb")
        
        # Always write the header
        self.tmp_fh.write(wav_header)
        self.bytes_written = len(wav_header)
        
        # Write any audio data
        if audio_data:
            self.tmp_fh.write(audio_data)
            self.bytes_written += len(audio_data)
        
        self.expected_seq = 1  # Next expected sequence number
        self.num_gaps = 0  # Reset gap counter
        
        log.info("Opened new WAV %s, initial size: %d bytes", self.tmp_path.name, self.bytes_written)

    def _calculate_duration(self, file_path: Path) -> float:
        """
        Calculate duration of WAV file in seconds.
        
        Args:
            file_path: Path to the WAV file
            
        Returns:
            Duration in seconds, or 0.0 if calculation fails
        """
        try:
            with file_path.open("rb") as f:
                # Read header to find data chunk
                f.seek(0)
                header = f.read(8192)
                idx = header.find(b"data")
                
                if idx >= 0 and len(header) >= idx + 8:
                    # Read data chunk size
                    f.seek(idx + 4)
                    data_size = struct.unpack("<I", f.read(4))[0]
                    duration = data_size / self.bytes_per_second
                    return duration
                else:
                    # Fallback: estimate based on file size minus standard header
                    file_size = file_path.stat().st_size
                    audio_bytes = max(0, file_size - 44)
                    return audio_bytes / self.bytes_per_second
                    
        except Exception as e:
            log.warning("Error calculating duration for %s: %s", file_path.name, e)
            return 0.0

    def _finalise(self):
        """Close temp file, patch headers, and move to final location or delete if too short."""
        if not self.tmp_fh:
            return
            
        self.tmp_fh.flush()
        self.tmp_fh.close()

        path = self.tmp_path
        total_size = path.stat().st_size
        riff_size = total_size - 8

        # Patch RIFF and data chunk sizes
        with path.open("r+b") as f:
            # Patch RIFF size at offset 4
            f.seek(4)
            f.write(struct.pack("<I", riff_size))

            # Find and patch data chunk size
            f.seek(0)
            header_bytes = f.read(8192)
            idx = header_bytes.find(b"data")
            
            if idx >= 0:
                data_size = total_size - (idx + 8)
                f.seek(idx + 4)
                f.write(struct.pack("<I", data_size))
            else:
                log.error("WAV header in %s has no 'data' chunk; skipping size patch", path.name)

            f.flush()
            os.fsync(f.fileno())

        # Calculate duration and check minimum
        duration = self._calculate_duration(path)
        log.debug("Recording %s: %.2f seconds, %d gaps", path.name, duration, self.num_gaps)

        if duration < config.MIN_RECORDING_DURATION_SEC:
            # Too short - delete it
            log.warning("Recording too short (%.2fs < %ds) - deleting %s", 
                       duration, config.MIN_RECORDING_DURATION_SEC, path.name)
            path.unlink()
        else:
            # Move to final location
            final_path = config.RECORDINGS_DIR / self.tmp_path.name[:-4]  # Remove .tmp
            shutil.move(self.tmp_path, final_path)
            log.info("Finalized recording %s (%.2fs, %d gaps)", final_path.name, duration, self.num_gaps)

            # Prepare payload for downstream processing
            payload = {
                "filename": final_path.name,
                "recorded_timestamp": self.start_ts,
                "listener_id": self.listener_id,
                "local_path": str(final_path),
                "num_gaps": self.num_gaps
            }
            
            # Enqueue jobs based on configuration
            if config.ANALYZE_RECORDINGS:
                redis_utils.enqueue_job("stream:analyze", payload)
            if config.UPLOAD_RAW_TO_CLOUD:
                redis_utils.enqueue_job("stream:upload", payload)
            if (not config.ANALYZE_RECORDINGS and 
                not config.UPLOAD_RAW_TO_CLOUD and 
                config.DELETE_RECORDINGS):
                # Mark as done for immediate deletion
                redis_utils.enqueue_job("stream:done", {**payload, "stage": "analyzed"})
                redis_utils.enqueue_job("stream:done", {**payload, "stage": "uploaded"})

        # Reset state
        self.tmp_fh = None
        self.tmp_path = None
        self.expected_seq = None
        self.start_ts = None
        self.bytes_written = 0
        self.num_gaps = 0

    def ingest_frame(self, seq: int, data: bytes):
        """
        Process one framed payload.
        
        Args:
            seq: 24-bit sequence number (0 to 16777215, with 0xFFFFFF for metadata)
            data: Frame payload data
        """
        # Skip metadata frames
        if seq == METADATA_SEQ:
            log.debug("Metadata frame (%d bytes) skipped", len(data))
            return
        
        # Check for WAV header in payload
        header_in_payload, audio_data = self._extract_wav_header_and_body(data)
        
        # Handle seq=0 (always starts new file)
        if seq == 0:
            log.info("New stream detected (seq=0%s)", " with header" if header_in_payload else "")
            self._finalise()  # Close any existing file
            self._open_new(header_in_payload, audio_data)
            return
        
        # Handle mid-stream header detection (indicates new recording)
        if header_in_payload and self.tmp_fh is not None:
            log.warning("WAV header detected mid-stream at seq=%d - starting new file", seq)
            self._finalise()
            self._open_new(header_in_payload, audio_data)
            # Continue with next expected sequence after this frame
            self.expected_seq = (seq + 1) % (1 << 24)
            return
        
        # Handle data frames without active file
        if self.tmp_fh is None:
            log.warning("Data frame (seq=%d) before header - dropping %d bytes", seq, len(data))
            return

        # Gap detection and handling
        if self.expected_seq is not None and seq != self.expected_seq:
            # Calculate gap size considering wraparound
            missing = (seq - self.expected_seq) % (1 << 24)
            
            if 0 < missing <= MAX_SILENT_PAGES:
                # Small gap - pad with silence
                log.warning("Gap detected: missing %d pages (expected %d, got %d) - padding with silence", 
                        missing, self.expected_seq, seq)
                silence_bytes = b"\x00" * PAGE_BYTES * missing
                self.tmp_fh.write(silence_bytes)
                self.bytes_written += len(silence_bytes)
                self.num_gaps += 1
            else:
                # Large gap - start new file
                log.warning("Large gap of %d pages (expected %d, got %d) - rolling to new file", 
                        missing, self.expected_seq, seq)
                self._finalise()
                # Wait for next header frame
                return

        # Write audio data
        self.tmp_fh.write(data)
        self.bytes_written += len(data)
        
        # Update expected sequence, handling wraparound
        self.expected_seq = (seq + 1) % (1 << 24)
        # Skip metadata sequence number when wrapping
        if self.expected_seq == METADATA_SEQ:
            self.expected_seq = 0
            log.debug("Sequence wrapped around to 0")

        # Check for size-based rollover
        if self.max_temp_bytes and self.bytes_written > self.max_temp_bytes:
            log.info("File size limit reached (%d > %d bytes) - rolling to new file",
                    self.bytes_written, self.max_temp_bytes)
            self._finalise()
            # Start new file with cached header
            if self.last_header:
                self._open_new(self.last_header, None)
                # Continue sequence numbering
                self.expected_seq = (seq + 1) % (1 << 24)
                if self.expected_seq == METADATA_SEQ:
                    self.expected_seq = 0


def clear_temp_folder():
    """Clear the temporary recordings folder on startup."""
    log.info("Clearing the temporary recordings folder")
    temp_path = config.RECORDINGS_TMP_DIR
    if temp_path.exists():
        shutil.rmtree(temp_path)
    temp_path.mkdir(parents=True, exist_ok=True)

clear_temp_folder()

# ─────────────────────────────────────────────────────────────────────────────
# FastAPI endpoints
# ─────────────────────────────────────────────────────────────────────────────
@app.post(recordings_stream_endpoint)
async def receive_stream(
    request: Request,
    listener_id: str = Query(..., description="Unique ID of recording node"),
):
    """
    Consume chunked HTTP body in real-time, parse 6-byte frame headers,
    and stream pages to disk via WaveAssembler.
    """
    log.info("New stream connection from listener: %s", listener_id)
    assembler = WaveAssembler(listener_id)
    buffer = bytearray()

    try:
        async for chunk in request.stream():
            buffer.extend(chunk)

            # Parse all complete frames in buffer
            while len(buffer) >= FRAME_HEADER_BYTES:
                # Extract frame header
                seq = int.from_bytes(buffer[0:3], "little")
                plen = int.from_bytes(buffer[3:6], "little")

                # Wait for complete frame
                if len(buffer) < FRAME_HEADER_BYTES + plen:
                    break

                # Extract payload and remove from buffer
                payload = bytes(buffer[6:6 + plen])
                del buffer[:6 + plen]

                # Process frame
                assembler.ingest_frame(seq, payload)

        # Connection closed - finalize any open file
        assembler._finalise()
        log.info("Stream from %s completed successfully", listener_id)
        return JSONResponse({"status": "ok", "listener": listener_id})

    except Exception as exc:
        log.exception("Stream from %s aborted", listener_id)
        assembler._finalise()
        raise HTTPException(500, f"Receiver error: {exc}") from exc


@app.post(recording_upload_endpoint)
async def upload_audio(
    file: UploadFile = File(...),
    listener_id: str | None = Form(None),
):
    """Handle direct audio file uploads (legacy endpoint)."""
    log.info("Audio upload received from %s: %s", listener_id, file.filename)
    
    # Validate file type
    if not file.filename.lower().endswith((".wav", ".flac", ".mp3")):
        raise HTTPException(400, "Unsupported file type")

    # Generate filename
    ts = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    base_name = Path(file.filename).name
    listener_id = listener_id or utils.get_listener_id_from_name(base_name)
    filename = f"{listener_id}_{ts}{Path(file.filename).suffix}"

    # Save file
    recording_path = config.RECORDINGS_DIR / filename
    with recording_path.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    
    log.info("Saved uploaded recording: %s", recording_path)

    # Prepare payload for downstream processing
    payload = {
        "filename": filename,
        "recorded_timestamp": ts,
        "listener_id": listener_id,
        "local_path": str(recording_path),
        "num_gaps": 0
    }
    
    # Enqueue jobs
    if config.ANALYZE_RECORDINGS:
        redis_utils.enqueue_job("stream:analyze", payload)
    if config.UPLOAD_RAW_TO_CLOUD:
        redis_utils.enqueue_job("stream:upload", payload)
    if (not config.ANALYZE_RECORDINGS and 
        not config.UPLOAD_RAW_TO_CLOUD and 
        config.DELETE_RECORDINGS):
        # Mark for deletion
        redis_utils.enqueue_job("stream:done", {**payload, "stage": "analyzed"})
        redis_utils.enqueue_job("stream:done", {**payload, "stage": "uploaded"})

    return {"status": "ok", "filename": filename}


log.info("Stream endpoint: http://localhost:8000%s", recordings_stream_endpoint)
log.info("Upload endpoint: http://localhost:8000%s", recording_upload_endpoint)