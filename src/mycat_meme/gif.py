"""ffmpeg / ffprobe subprocess wrappers used by the GIF replacement pipeline.

Public surface:
    FFMPEG_BINARY        — name of the ffmpeg binary (overridable for tests)
    FFPROBE_BINARY       — name of the ffprobe binary
    VideoMetadata        — dataclass returned by probe_video
    extract_first_frame(src, dest)  — extract frame 0 of an animation as PNG
    convert_to_mp4(src, dest)       — re-encode any animated input to mp4 (h264)
    convert_mp4_to_gif(src, dest, *, fps, max_width)
                                   — re-encode mp4 to optimized GIF (palette)
    probe_video(path) -> VideoMetadata
                                   — read width/height/duration via ffprobe

All functions raise FfmpegNotInstalled if the binary is missing, or FfmpegFailed
on non-zero exit.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mycat_meme.errors import FfmpegFailed, FfmpegNotInstalled

FFMPEG_BINARY = "ffmpeg"
FFPROBE_BINARY = "ffprobe"


@dataclass(frozen=True)
class VideoMetadata:
    """Metadata extracted from a video or animated image file via ffprobe."""

    width: int
    height: int
    duration_seconds: float


def _run(argv: list[str]) -> str:
    """Run a subprocess, return stdout, raise on missing binary or non-zero exit."""
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as e:
        raise FfmpegNotInstalled(
            f"binary not found on PATH (looked for {argv[0]!r})"
        ) from e
    if result.returncode != 0:
        raise FfmpegFailed(returncode=result.returncode, stderr=result.stderr)
    return result.stdout


def extract_first_frame(src: Path, dest: Path) -> Path:
    """Extract frame 0 of `src` (gif/mp4/etc.) to `dest` as a PNG."""
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            FFMPEG_BINARY,
            "-y",
            "-i",
            str(src),
            "-vframes",
            "1",
            "-f",
            "image2",
            str(dest),
        ]
    )
    return dest


def convert_to_mp4(src: Path, dest: Path) -> Path:
    """Re-encode any animated input (typically GIF) to an h264 mp4.

    Output is yuv420p / scaled to even dimensions so it's compatible with
    every consumer of mp4 input.
    """
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            FFMPEG_BINARY,
            "-y",
            "-i",
            str(src),
            "-movflags",
            "faststart",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-an",
            str(dest),
        ]
    )
    return dest


def convert_mp4_to_gif(
    src: Path,
    dest: Path,
    *,
    fps: int = 15,
    max_width: int = 600,
) -> Path:
    """Re-encode an mp4 to an optimized GIF using a 2-pass palette.

    fps and max_width are tuned for "shareable on social media" — small
    enough to upload, large enough to look decent.
    """
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"fps={fps},scale={max_width}:-1:flags=lanczos,"
        f"split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse"
    )
    _run(
        [
            FFMPEG_BINARY,
            "-y",
            "-i",
            str(src),
            "-vf",
            vf,
            str(dest),
        ]
    )
    return dest


def probe_video(path: Path) -> VideoMetadata:
    """Run ffprobe on `path` and return its width / height / duration."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"video not found: {path}")
    stdout = _run(
        [
            FFPROBE_BINARY,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise FfmpegFailed(
            returncode=0, stderr=f"ffprobe stdout was not valid JSON: {e}"
        ) from e

    streams = data.get("streams") or []
    if not streams:
        raise FfmpegFailed(
            returncode=0, stderr=f"ffprobe found no video streams in {path}"
        )
    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)

    duration_str = (data.get("format") or {}).get("duration") or stream.get("duration") or "0"
    try:
        duration = float(duration_str)
    except (TypeError, ValueError):
        duration = 0.0

    return VideoMetadata(width=width, height=height, duration_seconds=duration)


def ensure_ffmpeg_available() -> None:
    """Raise FfmpegNotInstalled if ffmpeg or ffprobe is not on PATH."""
    if shutil.which(FFMPEG_BINARY) is None:
        raise FfmpegNotInstalled(f"{FFMPEG_BINARY} not found on PATH")
    if shutil.which(FFPROBE_BINARY) is None:
        raise FfmpegNotInstalled(f"{FFPROBE_BINARY} not found on PATH")
