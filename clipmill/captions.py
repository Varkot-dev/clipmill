"""Burned-in captions as an ASS subtitle file.

Words appear a few at a time with the word being spoken highlighted, which is the
style that holds attention on Shorts.
"""

from __future__ import annotations

from pathlib import Path

from .transcribe import Word

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,7,2,5,60,60,0,1
Style: Handle,{font},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,4,1,8,40,40,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

HIGHLIGHT = r"{\c&H00FFFF&}"  # yellow (ASS colours are BGR)
RESET = r"{\c&HFFFFFF&}"


def ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def chunk(words: list[Word], size: int = 3, max_gap: float = 0.6) -> list[list[Word]]:
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= size or w.start - cur[-1].end > max_gap):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[Word], clip_start: float, clip_end: float, *, width: int = 1080,
              height: int = 1920, font: str = "Arial Black", words_per_chunk: int = 3,
              caption_y: int | None = None, handle_text: str = "") -> str:
    """words use window time; output uses clip time (window time minus clip_start)."""
    size = int(width * 0.085)
    out = [HEADER.format(w=width, h=height, font=font, size=size)]
    y = caption_y if caption_y is not None else int(height * 0.62)
    pos = rf"{{\an5\pos({width // 2},{y})}}"
    inside = [w for w in words if w.end > clip_start and w.start < clip_end]
    for group in chunk(inside, words_per_chunk):
        texts = [_escape(w.text).upper() for w in group]
        for i, w in enumerate(group):
            start = w.start - clip_start
            end = (group[i + 1].start if i + 1 < len(group) else w.end + 0.25) - clip_start
            line = " ".join(f"{HIGHLIGHT}{t}{RESET}" if j == i else t for j, t in enumerate(texts))
            out.append(f"Dialogue: 0,{ts(start)},{ts(end)},Cap,,0,0,0,,{pos}{line}")
    if handle_text:
        out.append(f"Dialogue: 1,{ts(0)},{ts(clip_end - clip_start)},Handle,,0,0,0,,{_escape(handle_text)}")
    return "\n".join(out) + "\n"


def write_ass(path: Path, *args, **kwargs) -> Path:
    path.write_text(build_ass(*args, **kwargs), encoding="utf-8")
    return path
