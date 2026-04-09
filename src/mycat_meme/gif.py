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

# dreamina multimodal2video --video reference constraints (from official docs).
# These are HARD constraints — violating them returns "final generation failed".
DREAMINA_VIDEO_MIN_PIXELS = 409_600       # 640×640 minimum total pixels
DREAMINA_VIDEO_MAX_PIXELS = 927_408       # 834×1112 maximum total pixels
DREAMINA_VIDEO_MIN_EDGE = 320             # min single edge (doc says 300, +20 margin)
DREAMINA_VIDEO_MAX_EDGE = 6000            # max single edge
DREAMINA_VIDEO_MIN_ASPECT = 0.4
DREAMINA_VIDEO_MAX_ASPECT = 2.5
DREAMINA_VIDEO_FPS = 30                   # in [24, 60] — 30 is the safe default
DREAMINA_VIDEO_TARGET_PIXELS = 700_000    # comfortable middle of [min, max]


# dreamina multimodal2video --image reference constraints (inferred — the
# official doc only published video limits, but image2video / multimodal2video
# empirically reject inputs outside roughly the same envelope).
DREAMINA_IMAGE_MIN_EDGE = 480
DREAMINA_IMAGE_MAX_EDGE = 1536
DREAMINA_IMAGE_MIN_ASPECT = 0.4
DREAMINA_IMAGE_MAX_ASPECT = 2.5


def _dreamina_safe_image_dimensions(orig_w: int, orig_h: int) -> tuple[int, int]:
    """Compute target (w, h) for an --image input to multimodal2video.

    - Aspect ratio preserved (clamped to [0.4, 2.5])
    - Each edge in [DREAMINA_IMAGE_MIN_EDGE, DREAMINA_IMAGE_MAX_EDGE]
    - Even dimensions
    """
    if orig_w <= 0 or orig_h <= 0:
        raise ValueError(f"invalid dimensions: {orig_w}x{orig_h}")

    aspect = orig_w / orig_h
    aspect = max(DREAMINA_IMAGE_MIN_ASPECT, min(DREAMINA_IMAGE_MAX_ASPECT, aspect))

    # Start at original size, fit into envelope
    w, h = float(orig_w), float(orig_h)

    # Upscale if too small
    min_edge = min(w, h)
    if min_edge < DREAMINA_IMAGE_MIN_EDGE:
        scale = DREAMINA_IMAGE_MIN_EDGE / min_edge
        w *= scale
        h *= scale

    # Downscale if too big
    max_edge = max(w, h)
    if max_edge > DREAMINA_IMAGE_MAX_EDGE:
        scale = DREAMINA_IMAGE_MAX_EDGE / max_edge
        w *= scale
        h *= scale

    # If the input aspect was outside [0.4, 2.5], we already clamped it which
    # means the dimensions need to match the clamped aspect. Recompute.
    if (orig_w / orig_h) != aspect:
        # Use the larger dim to anchor and recompute the other
        if w >= h:
            h = w / aspect
        else:
            w = h * aspect
        # Re-fit envelope
        max_edge = max(w, h)
        if max_edge > DREAMINA_IMAGE_MAX_EDGE:
            scale = DREAMINA_IMAGE_MAX_EDGE / max_edge
            w *= scale
            h *= scale

    return max(2, int(w / 2) * 2), max(2, int(h / 2) * 2)


def _dreamina_safe_video_dimensions(orig_w: int, orig_h: int) -> tuple[int, int]:
    """Compute target (w, h) that satisfies dreamina multimodal2video --video.

    - Aspect ratio is preserved (clamped to [0.4, 2.5] if outside)
    - Total pixel count lands near DREAMINA_VIDEO_TARGET_PIXELS, in [min, max]
    - Each edge is at least DREAMINA_VIDEO_MIN_EDGE
    - Returned dimensions are even (h264 requires)
    """
    if orig_w <= 0 or orig_h <= 0:
        raise ValueError(f"invalid dimensions: {orig_w}x{orig_h}")

    aspect = orig_w / orig_h
    aspect = max(DREAMINA_VIDEO_MIN_ASPECT, min(DREAMINA_VIDEO_MAX_ASPECT, aspect))

    # Target pixel count → derive height first, then width
    # pixels = w * h = (h * aspect) * h = h² * aspect → h = sqrt(pixels / aspect)
    target_h = (DREAMINA_VIDEO_TARGET_PIXELS / aspect) ** 0.5
    target_w = target_h * aspect

    # Enforce minimum single edge
    min_edge = min(target_w, target_h)
    if min_edge < DREAMINA_VIDEO_MIN_EDGE:
        scale = DREAMINA_VIDEO_MIN_EDGE / min_edge
        target_w *= scale
        target_h *= scale

    # If we now exceed max pixels, scale back down
    while target_w * target_h > DREAMINA_VIDEO_MAX_PIXELS:
        target_w *= 0.98
        target_h *= 0.98

    # Round to even
    w = max(2, int(target_w / 2) * 2)
    h = max(2, int(target_h / 2) * 2)
    return w, h


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
    """Re-encode any animated input (typically GIF) to an h264 mp4 that
    satisfies dreamina multimodal2video's `--video` constraints:

      - total pixels in [409600, 927408]
      - each edge in [300, 6000]
      - aspect ratio in [0.4, 2.5]
      - format mp4
      - fps in [24, 60]

    Small GIFs (e.g. 240×230) get upscaled with lanczos to ~720p-ish so
    dreamina's internal validator accepts them. Without this normalization
    dreamina returns 'final generation failed' with no further detail.
    """
    src = Path(src)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Probe the source to compute target dimensions
    meta = probe_video(src)
    target_w, target_h = _dreamina_safe_video_dimensions(meta.width, meta.height)

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
            f"scale={target_w}:{target_h}:flags=lanczos",
            "-r",
            str(DREAMINA_VIDEO_FPS),
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
