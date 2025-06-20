"""Mock source: generates white-noise WAV files every 5's.
Key change: we first write to RECORDINGS_TMP_DIR; once the file is fully
flushed & closed we atomically move it into RECORDINGS_DIR so downstream
watchers never see a half-written file."""

import time, datetime as dt, random, wave, os, shutil
from pathlib import Path
from . import config, utils

SAMPLE_RATE = 16_000   # 16 kHz mono
DURATION    = 5        # seconds per snippet


def generate_recording():
    ts        = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    filename  = f"listener01_{ts}.wav"
    tmp_path  = config.RECORDINGS_TMP_DIR / filename
    finalPath = config.RECORDINGS_DIR     / filename

    # write white‑noise
    frames = (random.randint(-32768, 32767) for _ in range(SAMPLE_RATE * DURATION))
    with wave.open(str(tmp_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(int(i).to_bytes(2, "little", signed=True) for i in frames))

    # move into live directory (atomic on same filesystem)
    # this prevents the analyzer from trying to read it before it is done being written
    shutil.move(tmp_path, finalPath)
    utils.ensure_row(filename)
    print("[receiver] new recording", finalPath)


def main():
    while True:
        generate_recording()
        time.sleep(10)

if __name__ == "__main__":
    main()