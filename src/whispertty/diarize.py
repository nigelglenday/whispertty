"""Speaker diarization via pyannote.audio.

Pipeline:
1. Run Whisper on the recording's WAV → JSON segments with timestamps.
2. Run pyannote.audio on the same WAV → diarization with speaker IDs.
3. For each Whisper segment, find the diarization speaker that overlaps
   most with that timestamp window. Label the segment with that speaker.
4. Write a labeled `.txt` file with timestamps + 'Speaker N:' prefixes.

Pyannote's `pyannote/speaker-diarization-3.1` model is gated on HF:
- User accepts terms at huggingface.co/pyannote/speaker-diarization-3.1
- Also at huggingface.co/pyannote/segmentation-3.0
- Generates a read token at huggingface.co/settings/tokens
- We persist the token in the whispertty config TOML.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

# Pyannote import is heavy (~5s, lots of torch). Defer to first use.
_PIPELINE = None


def _load_pipeline(token: str):
    """Load the pyannote speaker-diarization pipeline. Cached process-wide."""
    global _PIPELINE
    if _PIPELINE is not None:
        return _PIPELINE

    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        token=token,
    )

    # Run on Apple GPU when available (much faster on M-series).
    try:
        import torch

        if torch.backends.mps.is_available():
            pipeline.to(torch.device("mps"))
    except Exception:
        pass

    _PIPELINE = pipeline
    return pipeline


def diarize_audio(wav: Path, token: str) -> list[dict]:
    """Run pyannote diarization on a WAV. Returns a list of dicts with
    keys: start, end, speaker (e.g. 'SPEAKER_00')."""
    pipeline = _load_pipeline(token)
    annotation = pipeline(str(wav))

    segments: list[dict] = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        segments.append({
            "start": float(turn.start),
            "end": float(turn.end),
            "speaker": str(speaker),
        })
    return segments


def _dominant_speaker(
    start: float, end: float, diar_segments: Iterable[dict]
) -> str | None:
    """Of all diarization segments overlapping [start, end], return the
    speaker label with the most cumulative overlap, or None if no overlap."""
    overlaps: dict[str, float] = {}
    for d in diar_segments:
        ov_start = max(start, d["start"])
        ov_end = min(end, d["end"])
        overlap = ov_end - ov_start
        if overlap > 0:
            overlaps[d["speaker"]] = overlaps.get(d["speaker"], 0.0) + overlap
    if not overlaps:
        return None
    return max(overlaps, key=overlaps.get)


def merge(whisper_json: Path, diar_segments: list[dict], output_txt: Path) -> Path:
    """Merge Whisper JSON segments with diarization into a labeled `.txt`.

    Consecutive Whisper segments from the same speaker are concatenated.
    """
    data = json.loads(whisper_json.read_text())
    whisper_segs = [
        s for s in data.get("segments", []) if (s.get("text") or "").strip()
    ]

    labeled: list[dict] = []
    for ws in whisper_segs:
        speaker = _dominant_speaker(ws["start"], ws["end"], diar_segments) or "Unknown"
        labeled.append({
            "start": ws["start"],
            "end": ws["end"],
            "text": ws["text"].strip(),
            "speaker": speaker,
        })

    # Merge consecutive same-speaker segments (with small gap tolerance).
    merged: list[dict] = []
    for seg in labeled:
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
