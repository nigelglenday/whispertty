"""Record audio via ffmpeg + (optionally) BlackHole, with auto audio-device switching.

Mirrors the proven pattern in ~/.local/bin/call-record:
- ffmpeg avfoundation captures input device(s)
- For system audio capture, BlackHole 2ch is the source and a Multi-Output
  Device named "Record + Speakers" is set as the system output (so audio
  reaches both speakers and BlackHole simultaneously).
- SwitchAudioSource handles output device switching cleanly.

Recording is backgrounded via subprocess.Popen so the CLI can return
immediately. PID and metadata are persisted to ~/.whispertty.* files.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

PID_FILE = Path.home() / ".whispertty.pid"
META_FILE = Path.home() / ".whispertty.meta"

BLACKHOLE_NAME = "BlackHole 2ch"
MULTI_OUTPUT_NAME = "Record + Speakers"


@dataclass
class RecordingMeta:
    pid: int
    mode: str  # "mic" | "system"
    label: str | None
    started: float  # epoch seconds
    prev_output: str | None
    files: list[str]  # absolute paths to wav files (1 for mic, 2 for system)
    input_device: str | None = None  # system default input at start time


# Substrings that indicate an "input" device which would record silence
# instead of voice (loopback drivers, virtual audio cables, etc.).
_SUSPICIOUS_INPUTS = ("BlackHole", "Loopback", "Aggregate", "Multi-Output")


def is_suspicious_input(name: str | None) -> bool:
    if not name:
        return False
    return any(s.lower() in name.lower() for s in _SUSPICIOUS_INPUTS)


def _which(name: str) -> str:
    p = shutil.which(name)
    if p:
        return p
    # Common Homebrew locations
    for candidate in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if Path(candidate).exists():
            return candidate
    raise RuntimeError(f"required binary not found on PATH: {name}")


def _ffmpeg() -> str:
    return _which("ffmpeg")


def _switch_audio_source() -> str | None:
    """Return path to SwitchAudioSource, or None if missing."""
    try:
        return _which("SwitchAudioSource")
    except RuntimeError:
        return None


def get_current_output() -> str | None:
    """Return current default output device name, or None if unavailable."""
    bin_path = _switch_audio_source()
    if not bin_path:
        return None
    try:
        return subprocess.check_output([bin_path, "-c"], text=True).strip()
    except subprocess.SubprocessError:
        return None


def get_current_input() -> str | None:
    """Return current default input device name, or None if unavailable."""
    bin_path = _switch_audio_source()
    if not bin_path:
        return None
    try:
        return subprocess.check_output(
            [bin_path, "-t", "input", "-c"], text=True
        ).strip()
    except subprocess.SubprocessError:
        return None


def set_audio_output(name: str) -> bool:
    """Switch system output to `name`. Returns True on success."""
    bin_path = _switch_audio_source()
    if not bin_path:
        return False
    try:
        subprocess.run([bin_path, "-s", name], check=True, capture_output=True)
        return True
    except subprocess.SubprocessError:
        return False


def _list_avfoundation_devices() -> str:
    """ffmpeg's avfoundation device dump. Always exits non-zero, so we
    swallow the failure and return stderr for parsing."""
    try:
        proc = subprocess.run(
            [_ffmpeg(), "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.SubprocessError as e:
        raise RuntimeError(f"ffmpeg failed to list devices: {e}") from e
    return proc.stderr or proc.stdout


def find_blackhole_index() -> int | None:
    """Parse ffmpeg's audio device list to find BlackHole's index."""
    devices = _list_avfoundation_devices()
    in_audio_section = False
    for line in devices.splitlines():
        if "AVFoundation audio devices" in line:
            in_audio_section = True
            continue
        if not in_audio_section:
            continue
        m = re.search(r"\[(\d+)\]\s+(.+?)\s*$", line)
        if m and BLACKHOLE_NAME in m.group(2):
            return int(m.group(1))
    return None


def is_recording() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_meta() -> RecordingMeta | None:
    if not META_FILE.exists():
        return None
    try:
        data = json.loads(META_FILE.read_text())
        return RecordingMeta(**data)
    except (ValueError, TypeError):
        return None


def _write_meta(meta: RecordingMeta) -> None:
    META_FILE.write_text(json.dumps(meta.__dict__, indent=2))


def _clear_state() -> None:
    for p in (PID_FILE, META_FILE):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _stamp(label: str | None) -> str:
    base = time.strftime("%Y-%m-%d_%H-%M")
    if label:
        # Strip path-unsafe chars; keep alphanumerics, dashes, underscores.
        safe = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-_")
        if safe:
            return f"{base}_{safe}"
    return base


def start(
    transcripts_dir: Path,
    *,
    mode: str = "mic",
    label: str | None = None,
) -> RecordingMeta:
    """Start a backgrounded ffmpeg recording.

    mode='mic': single track, system default mic.
    mode='system': two tracks, BlackHole + mic, requires Multi-Output Device.
    """
    if is_recording():
        raise RuntimeError("Already recording. Run `whispertty stop` first.")

    transcripts_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp(label)

    ffmpeg = _ffmpeg()

    prev_output: str | None = None

    if mode == "system":
        if find_blackhole_index() is None:
            raise RuntimeError(
                f"{BLACKHOLE_NAME} not found. "
                "Install: brew install blackhole-2ch (then reboot). "
                "Then create a Multi-Output Device named "
                f"'{MULTI_OUTPUT_NAME}' in Audio MIDI Setup."
            )
        bh_idx = find_blackhole_index()
        prev_output = get_current_output()
        if prev_output != MULTI_OUTPUT_NAME:
            set_audio_output(MULTI_OUTPUT_NAME)

        remote_file = transcripts_dir / f"{stamp}_remote.wav"
        local_file = transcripts_dir / f"{stamp}_local.wav"
        cmd = [
            ffmpeg,
            "-f", "avfoundation", "-i", f":{bh_idx}",
            "-f", "avfoundation", "-i", ":default",
            "-map", "0:a", "-ac", "1", "-ar", "16000", str(remote_file),
            "-map", "1:a", "-ac", "1", "-ar", "16000", str(local_file),
            "-y",
        ]
        files = [str(remote_file), str(local_file)]
    elif mode == "mic":
        wav_file = transcripts_dir / f"{stamp}.wav"
        cmd = [
            ffmpeg,
            "-f", "avfoundation", "-i", ":default",
            "-ac", "1", "-ar", "16000",
            str(wav_file),
            "-y",
        ]
        files = [str(wav_file)]
    else:
        raise ValueError(f"unknown recording mode: {mode!r}")

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Give ffmpeg a moment to fail fast on bad device config.
    time.sleep(1.0)
    if proc.poll() is not None:
        if prev_output and mode == "system":
            set_audio_output(prev_output)
        raise RuntimeError(
            "ffmpeg exited immediately. Check audio devices and BlackHole setup."
        )

    meta = RecordingMeta(
        pid=proc.pid,
        mode=mode,
        label=label,
        started=time.time(),
        prev_output=prev_output,
        files=files,
        input_device=get_current_input(),
    )
    PID_FILE.write_text(str(proc.pid))
    _write_meta(meta)
    return meta


def stop() -> RecordingMeta:
    """Send SIGINT to ffmpeg, wait for it to flush, restore audio output."""
    meta = read_meta()
    if not meta:
        raise RuntimeError("No active recording.")

    pid = meta.pid
    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        pass

    # Wait up to 5s for ffmpeg to clean up and flush output buffers.
    for _ in range(50):
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            break
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    # Restore audio output (system mode only).
    if meta.prev_output and meta.prev_output != MULTI_OUTPUT_NAME:
        set_audio_output(meta.prev_output)

    return meta


def cleanup() -> None:
    _clear_state()
