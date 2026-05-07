"""Browse, find, and delete transcript files in the configured directory."""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config

# Filenames look like: 2026-05-07_08-24[_label].txt and may have sibling
# .wav (raw audio), .json (Whisper output), and _remote.wav / _local.wav
# from call-record's two-track flow.
_FILENAME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<time>\d{2}-\d{2})(?:_(?P<label>.+))?$"
)


@dataclass
class Transcript:
    path: Path
    stem: str
    timestamp: str  # human-readable
    label: str | None
    size: int
    mtime: float

    @property
    def short_label(self) -> str:
        return self.label or "(no label)"


def _parse_stem(stem: str) -> tuple[str | None, str | None]:
    """Parse 'YYYY-MM-DD_HH-MM_label' → ('YYYY-MM-DD HH:MM', 'label')."""
    # call-record uses an extra '_remote' or '_local' suffix on the WAV files;
    # the merged transcript drops those.
    m = _FILENAME_RE.match(stem)
    if not m:
        return None, None
    try:
        dt = datetime.strptime(f"{m['date']} {m['time']}", "%Y-%m-%d %H-%M")
        ts = dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        ts = f"{m['date']} {m['time']}"
    return ts, m["label"]


def list_transcripts() -> list[Transcript]:
    """Return all .txt transcripts in the configured dir, newest first.

    Excludes intermediate per-track files (`_remote.txt`, `_local.txt`) so the
    picker shows only merged/final transcripts.
    """
    d = config.transcripts_dir()
    if not d.is_dir():
        return []
    items: list[Transcript] = []
    for p in d.iterdir():
        if p.suffix != ".txt":
            continue
        stem = p.stem
        if stem.endswith("_remote") or stem.endswith("_local"):
            continue
        ts, label = _parse_stem(stem)
        items.append(
            Transcript(
                path=p,
                stem=stem,
                timestamp=ts or stem,
                label=label,
                size=p.stat().st_size,
                mtime=p.stat().st_mtime,
            )
        )
    items.sort(key=lambda t: t.mtime, reverse=True)
    return items


def find(stem_or_name: str) -> Transcript | None:
    """Find a transcript by exact stem or filename."""
    target = stem_or_name
    if target.endswith(".txt"):
        target = target[:-4]
    for t in list_transcripts():
        if t.stem == target:
            return t
    return None


def delete(stem: str) -> list[Path]:
    """Remove `<stem>.txt` and any sibling files (.wav, .json, _remote.*,
    _local.*). Returns the list of removed paths."""
    d = config.transcripts_dir()
    removed: list[Path] = []
    for f in d.iterdir():
        # Match the stem with any common suffix.
        if f.stem == stem or f.stem.startswith(stem + "_") or f.name.startswith(stem + "."):
            try:
                f.unlink()
                removed.append(f)
            except OSError:
                pass
    return removed
