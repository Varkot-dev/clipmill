"""Word-level transcripts with faster-whisper (runs locally, free)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass
class Word:
    start: float
    end: float
    text: str


@lru_cache(maxsize=1)
def _model(name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel  # imported lazily: heavy dependency

    if device == "auto":
        try:
            import ctranslate2

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return WhisperModel(name, device=device, compute_type=compute_type)


def transcribe(audio: Path, model: str = "small", device: str = "auto", compute_type: str = "auto") -> list[Word]:
    segments, _ = _model(model, device, compute_type).transcribe(
        str(audio), word_timestamps=True, vad_filter=True, language="en"
    )
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append(Word(float(w.start), float(w.end), w.word.strip()))
    return words


def as_timed_text(words: list[Word], every_s: float = 5.0) -> str:
    """Transcript with a [mm:ss] marker every few seconds, for the judge to read."""
    lines, current, mark = [], [], None
    for w in words:
        if mark is None or w.start - mark >= every_s:
            if current:
                lines.append(" ".join(current))
            mark = w.start
            current = [f"[{int(mark // 60):02d}:{mark % 60:04.1f}]"]
        current.append(w.text)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)
