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
Style: Cap,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,1,0,1,9,4,5,50,50,0,1
Style: Handle,{font},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,4,1,8,40,40,60,1
Style: Ad,{font},44,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,3,10,0,9,40,40,60,1
Style: Hook,{font},{hook_size},&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,1,0,0,0,100,100,0,0,3,18,0,8,60,60,170,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

HIGHLIGHT = r"{\c&H00E5FF&}"  # yellow (ASS colours are BGR)
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
              height: int = 1920, font: str = "Arial Black", words_per_chunk: int = 2,
              caption_y: int | None = None, handle_text: str = "", hook: str = "",
              disclosure: str = "", disclosure_seconds: float = 3.0) -> str:
    """words use window time; output uses clip time (window time minus clip_start).

    hook: short context line shown in a white box at the top for the whole clip.
    """
    size = int(width * 0.10)
    out = [HEADER.format(w=width, h=height, font=font, size=size, hook_size=int(width * 0.06))]
    if disclosure.strip():  # paid-clip disclosure, top right, required at the start of the video
        end = min(disclosure_seconds, clip_end - clip_start)
        out.append(f"Dialogue: 3,{ts(0)},{ts(end)},Ad,,0,0,0,,{_escape(disclosure.strip())}")
    if hook.strip():
        out.append(f"Dialogue: 2,{ts(0)},{ts(clip_end - clip_start)},Hook,,0,0,0,,{_escape(hook.strip())}")
    y = caption_y if caption_y is not None else int(height * 0.62)
    pos = rf"{{\an5\pos({width // 2},{y})}}"
    inside = [w for w in words if w.end > clip_start and w.start < clip_end]
    groups = chunk(inside, words_per_chunk)
    for gi, group in enumerate(groups):
        nxt = groups[gi + 1][0].start if gi + 1 < len(groups) else None
        texts = [_escape(w.text).upper() for w in group]
        for i, w in enumerate(group):
            start = w.start - clip_start
            if i + 1 < len(group):
                end = group[i + 1].start - clip_start
            else:  # hold the last word briefly, but never overlap the next chunk
                end = (min(w.end + 0.25, nxt) if nxt is not None else w.end + 0.25) - clip_start
            line = " ".join(f"{HIGHLIGHT}{t}{RESET}" if j == i else t for j, t in enumerate(texts))
            pop = r"{\fscx70\fscy70\t(0,90,\fscx100\fscy100)}" if i == 0 else ""
            out.append(f"Dialogue: 0,{ts(start)},{ts(end)},Cap,,0,0,0,,{pos}{pop}{line}")
    if handle_text:
        out.append(f"Dialogue: 1,{ts(0)},{ts(clip_end - clip_start)},Handle,,0,0,0,,{_escape(handle_text)}")
    return "\n".join(out) + "\n"


def write_ass(path: Path, *args, **kwargs) -> Path:
    path.write_text(build_ass(*args, **kwargs), encoding="utf-8")
    return path
