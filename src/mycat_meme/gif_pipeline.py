"""High-level GIF replacement pipeline (v0.2).

The flow:

    input.gif + cat.jpg
        │
        ├── ffmpeg extract frame 0 of input.gif → first.png
        ├── ffmpeg convert input.gif → motion_ref.mp4    (h264, yuv420p)
        ├── ffprobe motion_ref.mp4 → width, height, duration
        │
        ├── pipeline.replace(meme=first.png, cat=cat.jpg) → replaced_first_full.png
        │       (this is the existing v0.1 flow: dreamina image2image)
        │       Output is typically a ~14 MB 5K PNG.
        │
        ├── Pillow downscale → replaced_first.jpg
        │       (dreamina multimodal2video upload fails on huge files —
        │        we keep the new cat's appearance but go to ~1280px JPEG)
        │
        ├── dreamina multimodal2video
        │       --image replaced_first.jpg
        │       --video motion_ref.mp4
        │       --duration <rounded input duration>
        │       --ratio <video-supported ratio nearest input>
        │       → JSON with result_json.videos[0].video_url
        │
        ├── download mp4 → result.mp4
        ├── ffmpeg result.mp4 → output.gif (palette-optimized)
        │
        ▼
    output.gif

All temporary files are placed in a tempfile.TemporaryDirectory() and cleaned
up automatically when the function returns.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

from PIL import Image

from mycat_meme.dreamina import (
    Image2ImageStillPending,
    download_image,
    parse_video_result,
    run_multimodal2video,
    wait_for_video_result,
)
from mycat_meme.errors import DreaminaCallFailed
from mycat_meme.gif import (
    convert_mp4_to_gif,
    convert_to_mp4,
    extract_first_frame,
    probe_video,
)
from mycat_meme.pipeline import replace as image_replace
from mycat_meme.prompts import DEFAULT_STYLE
from mycat_meme.ratio import VIDEO_SUPPORTED_RATIOS, detect_ratio

# Default seedance model — fastest of the seedance2.0 family. Quality is good
# enough for cat memes and the round-trip is well under a minute on most days.
DEFAULT_VIDEO_MODEL = "seedance2.0fast"

# Output GIF tuning. 15 fps + 600px wide = good size/quality balance.
DEFAULT_OUTPUT_FPS = 15
DEFAULT_OUTPUT_MAX_WIDTH = 600

# Hard upper bound on what we'll send as --duration to multimodal2video.
# dreamina enforces 4-15s; longer inputs get clipped.
MIN_VIDEO_DURATION = 4
MAX_VIDEO_DURATION = 15

# Max width for the intermediate "replaced first frame" we feed to
# multimodal2video. dreamina's upload step rejects very large files (a 14MB
# 5K PNG from image2image will fail with "upload phase, no file upload"),
# so we downscale to JPEG before passing it forward. 1280px is plenty for
# the model to read appearance details.
MAX_INTERMEDIATE_WIDTH = 1280

# The motion-aware video prompt for cat replacement. Static-image prompts
# (in prompts.py) describe "the cat in image 1 vs image 2" — but for video
# we have only one image (the already-replaced first frame). The motion
# reference is a separate --video input. So this prompt just tells the model
# to follow the input video's motion while keeping the new cat's appearance.
_VIDEO_PROMPT = (
    "保持参考图像中猫的外观和场景不变，"
    "让猫按照参考视频中的动作和节奏自然运动，"
    "镜头和背景与参考图像保持一致。"
)

# Substrings in dreamina stderr that indicate a transient/retryable error
# (network blip on dreamina's own backend, not a user error).
_TRANSIENT_ERROR_MARKERS = (
    "context deadline exceeded",
    "connection reset",
    "EOF",
    "i/o timeout",
    "no such host",
    "tls handshake",
)


def _is_transient_dreamina_error(stderr: str) -> bool:
    return any(m in stderr.lower() for m in (s.lower() for s in _TRANSIENT_ERROR_MARKERS))


def _run_multimodal2video_with_retry(
    *,
    image: Path,
    video: Path,
    prompt: str,
    duration: int,
    ratio: str,
    model_version: str,
    poll_seconds: int,
    max_attempts: int = 3,
) -> str:
    """Call run_multimodal2video, retrying transient network failures.

    dreamina's own backend occasionally drops the long-poll HTTP connection
    with errors like 'context deadline exceeded'. Retry up to N times before
    surfacing the failure.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return run_multimodal2video(
                image=image,
                video=video,
                prompt=prompt,
                duration=duration,
                ratio=ratio,
                model_version=model_version,
                poll_seconds=poll_seconds,
            )
        except DreaminaCallFailed as e:
            last_exc = e
            if not _is_transient_dreamina_error(e.stderr):
                raise
            if attempt == max_attempts:
                raise
            # else: fall through and retry
    # Defensive — should be unreachable
    assert last_exc is not None
    raise last_exc


def _round_duration(seconds: float) -> int:
    """Clamp and round a float duration to dreamina's allowed integer range."""
    if seconds <= 0:
        return MIN_VIDEO_DURATION
    rounded = max(MIN_VIDEO_DURATION, min(MAX_VIDEO_DURATION, int(math.ceil(seconds))))
    return rounded


def _downscale_for_upload(src: Path, dest: Path, max_width: int = MAX_INTERMEDIATE_WIDTH) -> Path:
    """Re-encode `src` as a JPEG of at most `max_width` pixels wide.

    dreamina's multimodal2video upload step fails on very large PNGs from
    image2image. This function shrinks them to a size dreamina accepts while
    preserving enough resolution for the model to read the cat's appearance.
    """
    with Image.open(src) as img:
        img = img.convert("RGB")
        if img.width > max_width:
            ratio = max_width / img.width
            new_size = (max_width, int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "JPEG", quality=90, optimize=True)
    return dest


def replace_gif(
    *,
    gif: Path,
    cat: Path,
    output: Path,
    style: str = DEFAULT_STYLE,
    model_version: str = DEFAULT_VIDEO_MODEL,
    duration: int | None = None,
    output_fps: int = DEFAULT_OUTPUT_FPS,
    output_max_width: int = DEFAULT_OUTPUT_MAX_WIDTH,
    poll_seconds: int = 240,
) -> Path:
    """Replace the cat in `gif` with the cat in `cat`, writing to `output`.

    Args:
        gif: Path to the input GIF (or any animated format ffmpeg can read).
        cat: Path to the user's cat photo.
        output: Where to write the result GIF.
        style: Static-replacement prompt style for the first-frame replacement.
        model_version: dreamina seedance2.0 family member.
        duration: Output video length in seconds. If None, derive from input.
        output_fps: GIF frame rate.
        output_max_width: GIF width in pixels (height auto-scaled).
        poll_seconds: Inline poll budget for multimodal2video.

    Returns:
        The `output` path on success.

    Raises:
        FileNotFoundError: if input files don't exist.
        FfmpegNotInstalled / FfmpegFailed: from gif.py.
        DreaminaNotInstalled / DreaminaCallFailed / OutputNotFound: from
            dreamina.py — propagated as-is so callers can map to exit codes.
    """
    gif = Path(gif)
    cat = Path(cat)
    output = Path(output)

    if not gif.exists():
        raise FileNotFoundError(f"input gif not found: {gif}")
    if not cat.exists():
        raise FileNotFoundError(f"cat image not found: {cat}")

    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mycat-meme-") as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        first_frame = tmpdir / "first.png"
        motion_ref = tmpdir / "motion_ref.mp4"
        replaced_first_full = tmpdir / "replaced_first_full.png"
        replaced_first = tmpdir / "replaced_first.jpg"
        result_mp4 = tmpdir / "result.mp4"

        # 1) Extract first frame and convert input to mp4 motion reference
        extract_first_frame(gif, first_frame)
        convert_to_mp4(gif, motion_ref)

        # 2) Probe the motion reference for duration + dimensions
        meta = probe_video(motion_ref)
        ratio = detect_ratio(
            meta.width, meta.height, supported=VIDEO_SUPPORTED_RATIOS
        )
        effective_duration = duration if duration is not None else _round_duration(
            meta.duration_seconds
        )
        effective_duration = max(
            MIN_VIDEO_DURATION, min(MAX_VIDEO_DURATION, effective_duration)
        )

        # 3) Static replace on the first frame (reuses the v0.1 image pipeline)
        image_replace(
            meme=first_frame,
            cat=cat,
            output=replaced_first_full,
            style=style,
        )

        # 3b) Downscale the (typically 5K, ~14MB) result for upload — dreamina
        # multimodal2video rejects huge files at the upload step.
        _downscale_for_upload(replaced_first_full, replaced_first)

        # 4) Animate the replaced first frame following the motion reference
        stdout = _run_multimodal2video_with_retry(
            image=replaced_first,
            video=motion_ref,
            prompt=_VIDEO_PROMPT,
            duration=effective_duration,
            ratio=ratio,
            model_version=model_version,
            poll_seconds=poll_seconds,
        )

        try:
            video_result = parse_video_result(stdout)
        except Image2ImageStillPending as pending:
            video_result = wait_for_video_result(pending.submit_id)

        # 5) Download mp4, convert to GIF
        download_image(video_result.video_url, result_mp4)
        convert_mp4_to_gif(
            result_mp4,
            output,
            fps=output_fps,
            max_width=output_max_width,
        )

    return output
