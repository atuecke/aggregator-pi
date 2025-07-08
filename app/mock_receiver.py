"""Mock source: generates white-noise WAV files every 5's.
Key change: we first write to RECORDINGS_TMP_DIR; once the file is fully
flushed & closed we atomically move it into RECORDINGS_DIR so downstream
watchers never see a half-written file."""

import time, datetime as dt, random, wave, os, shutil
from pathlib import Path
from . import config, utils, redis_utils

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
utils.setup_logging()  # Initialise global logging once per process
import logging
log = logging.getLogger("app.mock_receiver")
# ---------------------------------------------------------------------------


SAMPLE_RATE = 16_000   # 16 kHz mono
DURATION    = 5        # seconds per snippet

listeners = [
    "listener01",
    "listener02",
    "listener03",
]

def generate_recording():
    listener_name = random.choice(listeners)
    ts        = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    filename  = f"{listener_name}_{ts}.wav"
    tmp_path  = config.RECORDINGS_TMP_DIR / filename
    final_path = config.RECORDINGS_DIR    / filename

    # write white‑noise
    frames = (random.randint(-32768, 32767) for _ in range(SAMPLE_RATE * DURATION))
    with wave.open(str(tmp_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(int(i).to_bytes(2, "little", signed=True) for i in frames))
    log.debug("Saved new recording to temporary file %s", tmp_path)
    
    payload = {
        "filename": filename,
        "listener_id": listener_name,
        "local_path": str(final_path)
    }
    redis_utils.enqueue_job("stream:analyze", payload)
    redis_utils.enqueue_job("stream:upload",  payload)

    # move into live directory (atomic on same filesystem)
    # this prevents the analyzer from trying to read it before it is done being written
    shutil.move(tmp_path, final_path)

    log.info("New recording %s", final_path)


def main():
        while True:
            if config.GENERATE_MOCK_AUDIO:
                generate_recording()
            time.sleep(10)

if __name__ == "__main__":
    if(config.GENERATE_MOCK_AUDIO):
        main()
    else:
        log.info("Generating mock audio has been disabled. Re-enable it in settings to generate mock white noise audio files")
        time.sleep(5) # Avoid supervisord running check failing