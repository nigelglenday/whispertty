"""Whisper invocation and (when applicable) two-track speaker-label merge."""

import json
import re
import shutil
import subprocess
import wave
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

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


def wav_duration_seconds(path: Path) -> float:
    """Return WAV duration in seconds. 0.0 if the file isn't a readable WAV."""
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, OSError, EOFError):
        return 0.0


# Whisper streams lines like '[00:00.000 --> 00:05.000]  text...' as it
# processes. The second timestamp gives us "how far through the audio
# we've gotten" — divide by total duration for percent done.
_TS_RE = re.compile(
    r"\[(?:(\d+):)?(\d+):(\d+)\.(\d+)\s*-->\s*(?:(\d+):)?(\d+):(\d+)\.(\d+)\]"
)


def _parse_end_seconds(line: str) -> float | None:
    m = _TS_RE.search(line)
    if not m:
        return None
    end_h = int(m.group(5)) if m.group(5) else 0
    end_m = int(m.group(6))
    end_s = int(m.group(7))
    end_ms = int(m.group(8))
    return end_h * 3600 + end_m * 60 + end_s + end_ms / 1000.0


def _run_whisper_with_progress(
    cmd: list[str],
    duration: float,
    description: str,
    console: Console,
) -> None:
    """Spawn Whisper, stream stdout+stderr, drive a Rich Progress bar from
    the timestamps it emits. Raises RuntimeError on non-zero exit."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    columns = [
        SpinnerColumn(style="cyan"),
        TextColumn(f"[nav]{description}[/nav]"),
        BarColumn(bar_width=None),
        TextColumn("{task.percentage:>3.0f}%"),
        TextColumn("•"),
        TimeElapsedColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
    ]

    last_stderr: list[str] = []
    total = max(duration, 1.0)

    with Progress(*columns, console=console, transient=True) as progress:
        task = progress.add_task("transcribe", total=total)
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            last_stderr.append(line)
            if len(last_stderr) > 50:
                last_stderr.pop(0)
            end = _parse_end_seconds(line)
            if end is not None:
                progress.update(task, completed=min(end, total))
        proc.wait()
        progress.update(task, completed=total)

    if proc.returncode != 0:
        tail = "\n".join(last_stderr[-15:])
        raise RuntimeError(
            f"whisper failed (exit {proc.returncode}). last output:\n{tail}"
        )


def transcribe_file(
    wav: Path,
    output_dir: Path,
    model: str = "base",
    *,
    console: Console | None = None,
    description: str = "Transcribing",
) -> Path:
    """Run Whisper on `wav`, output JSON to `output_dir`. Returns JSON path.

    If `console` is provided, shows a Rich Progress bar driven by Whisper's
    timestamp stream. Otherwise runs silently.
    """
    whisper = _find_whisper()
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        whisper,
        str(wav),
        "--model", model,
        "--output_format", "json",
        "--output_dir", str(output_dir),
    ]
    if console is not None:
        duration = wav_duration_seconds(wav)
        _run_whisper_with_progress(cmd, duration, description, console)
    else:
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


def transcribe_one_track(
    wav: Path,
    output_dir: Path,
    model: str = "base",
    *,
    console: Console | None = None,
    description: str = "Transcribing",
) -> Path:
    """Transcribe a single-track recording. Returns the .txt path."""
    whisper = _find_whisper()
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        whisper,
        str(wav),
        "--model", model,
        "--output_format", "txt",
        "--output_dir", str(output_dir),
    ]
    if console is not None:
        duration = wav_duration_seconds(wav)
        _run_whisper_with_progress(cmd, duration, description, console)
    else:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"whisper failed (exit {proc.returncode}). stderr:\n{proc.stderr[-2000:]}"
            )
    return output_dir / f"{wav.stem}.txt"
