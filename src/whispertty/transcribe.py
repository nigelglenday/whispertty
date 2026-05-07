"""Whisper invocation and (when applicable) two-track speaker-label merge."""

import json
import shutil
import subprocess
from pathlib import Path

# Whisper installs to either /Library/Frameworks/Python... (Python.org installer)
# or somewhere on PATH (homebrew Python, conda, etc). Probe both.
_WHISPER_CANDIDATES = [
    "/Library/Frameworks/Python.framework/Versions/3.14/bin/whisper",
    "/Library/Frameworks/Python.framework/Versions/3.13/bin/whisper",
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/whisper",
    "/Library/Frameworks/Python.framework/Versions/3.11/bin/whisper",
    "/opt/homebrew/bin/whisper",
    "/usr/local/bin/whisper",
]


def _find_whisper() -> str:
    p = shutil.which("whisper")
    if p:
        return p
    for c in _WHISPER_CANDIDATES:
        if Path(c).exists():
            return c
    raise RuntimeError(
        "whisper binary not found. Install with: pip install -U openai-whisper"
    )


def transcribe_file(
    wav: Path,
    output_dir: Path,
    model: str = "base",
) -> Path:
    """Run Whisper on `wav`, output JSON to `output_dir`. Return JSON path."""
    whisper = _find_whisper()
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        whisper,
        str(wav),
        "--model", model,
        "--output_format", "json",
        "--output_dir", str(output_dir),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"whisper failed (exit {proc.returncode}). stderr:\n{proc.stderr[-2000:]}"
        )
    return output_dir / f"{wav.stem}.json"


def merge_two_tracks(
    remote_json: Path,
    local_json: Path,
    output_txt: Path,
    *,
    remote_label: str = "Remote",
    local_label: str = "Local",
) -> Path:
    """Merge two Whisper JSON outputs into a single labeled `.txt`.

    Same algorithm as call-record's inline Python merge.
    """

    def load(path: Path, speaker: str) -> list[dict]:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return []
        out = []
        for s in data.get("segments", []):
            text = (s.get("text") or "").strip()
            if not text:
                continue
            out.append({
                "start": s["start"],
                "end": s["end"],
                "text": text,
                "speaker": speaker,
            })
        return out

    segments = load(remote_json, remote_label) + load(local_json, local_label)
    segments.sort(key=lambda s: s["start"])

    merged: list[dict] = []
    for seg in segments:
        if (
            merged
            and merged[-1]["speaker"] == seg["speaker"]
            and seg["start"] - merged[-1]["end"] < 2.0
        ):
            merged[-1]["text"] += " " + seg["text"]
            merged[-1]["end"] = seg["end"]
        else:
            merged.append(dict(seg))

    with output_txt.open("w") as f:
        for seg in merged:
            mins = int(seg["start"] // 60)
            secs = int(seg["start"] % 60)
            f.write(f'[{mins:02d}:{secs:02d}] {seg["speaker"]}: {seg["text"]}\n\n')

    return output_txt


def transcribe_one_track(wav: Path, output_dir: Path, model: str = "base") -> Path:
    """Transcribe a single-track recording. Whisper produces .txt next to .json;
    we just return the .txt path that Whisper writes.
    """
    whisper = _find_whisper()
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        whisper,
        str(wav),
        "--model", model,
        "--output_format", "txt",
        "--output_dir", str(output_dir),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"whisper failed (exit {proc.returncode}). stderr:\n{proc.stderr[-2000:]}"
        )
    return output_dir / f"{wav.stem}.txt"
