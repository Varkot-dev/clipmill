"""Turns a 16:9 window into a finished 9:16 Short with ffmpeg."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _box(region: list[float]) -> str:
    x, y, w, h = region
    return f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y}"


def _fill(w: int, h: int) -> str:
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"


def _zoom(region: list[float], keep: float = 0.72) -> list[float]:
    """Crops the sides of a full-width frame so the gameplay is bigger on a phone."""
    x, y, w, h = region
    if w < 0.99:
        return region
    return [x + w * (1 - keep) / 2, y, w * keep, h]


def vertical_filter(facecam: list[float], gameplay: list[float], w: int = 1080, h: int = 1920,
                    cam_share: float = 0.35) -> tuple[str, int]:
    """Returns (filtergraph ending in [v], y position for captions)."""
    if facecam:
        cam_h = int(h * cam_share) // 2 * 2
        game_h = h - cam_h
        graph = (
            f"[0:v]split=2[a][b];"
            f"[a]{_box(facecam)},{_fill(w, cam_h)}[cam];"
            f"[b]{_box(gameplay)},{_fill(w, game_h)}[game];"
            f"[cam][game]vstack=inputs=2[v]"
        )
        return graph, cam_h + int(game_h * 0.30)
    # No camera: sharp gameplay in the middle over a blurred copy of itself.
    graph = (
        f"[0:v]split=2[a][b];"
        f"[a]{_fill(w, h)},boxblur=20:2[bg];"
        f"[b]{_box(_zoom(gameplay))},scale={w}:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
    )
    return graph, int(h * 0.75)


def render_short(src: Path, out: Path, start: float, end: float, ass: Path | None, *,
                 facecam: list[float], gameplay: list[float], width: int = 1080, height: int = 1920,
                 crf: int = 20, preset: str = "veryfast") -> Path:
    graph, _ = vertical_filter(facecam, gameplay, width, height)
    if ass is not None:
        escaped = str(ass).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        graph = graph.replace("[v]", "[pre]") + f";[pre]ass='{escaped}'[v]"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
        "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart", str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def caption_y(facecam: list[float], width: int = 1080, height: int = 1920) -> int:
    return vertical_filter(facecam, [0, 0, 1, 1], width, height)[1]


def compile_long(clips: list[Path], out: Path, crf: int = 20, preset: str = "veryfast") -> Path:
    """Joins finished clips into one video (for weekly 'best of' uploads)."""
    if not clips:
        raise ValueError("no clips to compile")
    inputs, parts = [], []
    for i, c in enumerate(clips):
        inputs += ["-i", str(c)]
        parts.append(f"[{i}:v]setsar=1[v{i}];[{i}:a]aresample=48000[a{i}]")
    concat = "".join(f"[v{i}][a{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=1[v][a]"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(parts) + ";" + concat,
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)],
        check=True,
    )
    return out
