"""High-level GIF replacement pipeline (v0.2.1).

The flow:

    input.gif + cat.jpg
        │
        ├── ffmpeg extract frame 0 of input.gif → first_frame.png
        ├── ffmpeg convert input.gif → motion_ref.mp4    (h264, yuv420p)
        ├── ffprobe motion_ref.mp4 → width, height, duration
        │
        ├── dreamina multimodal2video
        │       --image first_frame.png   (composition + scene reference)
        │       --image cat.jpg            (appearance reference)
        │       --video motion_ref.mp4     (motion timing reference)
        │       --duration <rounded input duration>
        │       --ratio <video-supported ratio>
        │       --model_version seedance2.0fast
        │       --poll 0  (we poll query_result ourselves; see below)
        │
        ├── wait_for_video_result(submit_id)  (small HTTP polls; robust)
        │
        ├── download mp4 → result.mp4
        ├── ffmpeg result.mp4 → output.gif (palette-optimized)
        │
        ▼
    output.gif

## Architecture history

**v0.2.0** had a two-step pipeline: image2image to replace the cat in the
first frame, then multimodal2video to animate it. The first step was
unreliable when the input GIF's cat was a silhouette or low-contrast —
image2image produced a generic dark cat that lost the user's cat's
appearance, and the video step then amplified the loss.

**v0.2.1** skips image2image entirely. Empirically, multimodal2video does
a better job of "use the second image's cat appearance, the first image's
scene, and the video's motion" than the chained image2image+multimodal2video
approach. It's also faster (one dreamina call) and cheaper (~20 credits
instead of ~30).

## Reliability

dreamina's own internal long-poll for video tasks is unreliable (regularly
fails with `context deadline exceeded` from its own backend). We bypass it
by passing `--poll 0` to multimodal2video, which makes dreamina just
submit and return the submit_id. Then we poll `dreamina query_result`
ourselves via `wait_for_video_result` — many small HTTP calls instead of
one giant long-poll. wait_for_video_result also swallows transient
network errors during polling.

The HTTP download of the result mp4 has its own retry inside
`download_image` for transient SSL/connection errors.

All temporary files are placed in a tempfile.TemporaryDirectory() and
cleaned up automatically when the function returns.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path

from mycat_meme.dreamina import (
    Image2ImageStillPending,
    download_image,
    parse_video_result,
    run_multimodal2video,
    wait_for_video_result,
)
from mycat_meme.errors import DreaminaCallFailed, OutputNotFound
from mycat_meme.gif import (
    convert_mp4_to_gif,
    convert_to_mp4,
    extract_first_frame,
    probe_video,
)
from mycat_meme.ratio import VIDEO_SUPPORTED_RATIOS, detect_ratio

# Default seedance model — fastest of the seedance2.0 family. Quality is
# good enough for cat memes and round-trip is well under 6 minutes.
DEFAULT_VIDEO_MODEL = "seedance2.0fast"

# Output GIF tuning. 15 fps + 600px wide = good size/quality balance.
DEFAULT_OUTPUT_FPS = 15
DEFAULT_OUTPUT_MAX_WIDTH = 600

# dreamina enforces 4-15s on multimodal2video duration; longer inputs clip.
MIN_VIDEO_DURATION = 4
MAX_VIDEO_DURATION = 15

# The multimodal video prompt. v0.2.1 made this much more aggressive than
# v0.2.0 because the model was reverting to the motion reference's cat
# appearance (silhouettes), losing the user's cat features.
#
# This prompt explicitly tells the model:
# 1. The output cat must look like the cat in the SECOND image (the user's cat)
# 2. The first image only provides scene/composition
# 3. The video only provides motion timing
# 4. The cat must NOT be a silhouette or pure black
_VIDEO_PROMPT = (
    "输出视频中的猫必须严格按照第二张参考图中的猫的样子——"
    "保留第二张图里猫的毛色、花纹、白色胸部和爪子（如果有）、"
    "面部特征、品种。第一张参考图只提供场景和构图。"
    "参考视频提供动作时序和镜头运动。"
    "绝对不要让输出的猫变成剪影或纯黑色，"
    "必须像第二张图里的猫一样清晰可见，有正常的光照和色彩。"
)

# Substrings indicating a transient/retryable dreamina backend error.
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


def _retry_transient(callable_, *, max_attempts: int = 3):
    """Call `callable_()`, retrying on transient dreamina network errors.

    Catches both DreaminaCallFailed (non-zero exit) and OutputNotFound
    (gen_status="fail" with a transient fail_reason). Re-raises if the
    error is non-transient or after max_attempts.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return callable_()
        except DreaminaCallFailed as e:
            last_exc = e
            text = e.stderr
        except OutputNotFound as e:
            last_exc = e
            text = str(e)
        if not _is_transient_dreamina_error(text):
            raise last_exc
        if attempt == max_attempts:
            raise last_exc
        # else: loop and retry
    assert last_exc is not None
    raise last_exc


def _round_duration(seconds: float) -> int:
    """Clamp and round a float duration to dreamina's allowed integer range."""
    if seconds <= 0:
        return MIN_VIDEO_DURATION
    return max(MIN_VIDEO_DURATION, min(MAX_VIDEO_DURATION, int(math.ceil(seconds))))


def replace_gif(
    *,
    gif: Path,
    cat: Path,
    output: Path,
    model_version: str = DEFAULT_VIDEO_MODEL,
    duration: int | None = None,
    output_fps: int = DEFAULT_OUTPUT_FPS,
    output_max_width: int = DEFAULT_OUTPUT_MAX_WIDTH,
    poll_seconds: int = 600,
) -> Path:
    """Replace the cat in `gif` with the cat in `cat`, writing to `output`.

    Args:
        gif: Path to the input GIF (or any animated format ffmpeg can read).
        cat: Path to the user's cat photo.
        output: Where to write the result GIF.
        model_version: dreamina seedance2.0 family member.
        duration: Output video length in seconds. If None, derive from input.
        output_fps: GIF frame rate.
        output_max_width: GIF width in pixels (height auto-scaled).
        poll_seconds: Max wait time (seconds) for the video task to finish.

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

        # 3) One multimodal2video call with all three references in one shot.
        # Pass poll_seconds=0 — dreamina's internal long-poll is unreliable;
        # we poll query_result ourselves below for robustness.
        stdout = _retry_transient(
            lambda: run_multimodal2video(
                images=[first_frame, cat],
                videos=[motion_ref],
                prompt=_VIDEO_PROMPT,
                duration=effective_duration,
                ratio=ratio,
                model_version=model_version,
                poll_seconds=0,
            )
        )

        try:
            video_result = parse_video_result(stdout)
        except Image2ImageStillPending as pending:
            video_result = wait_for_video_result(
                pending.submit_id,
                max_wait_seconds=poll_seconds,
            )

        # 4) Download mp4, convert to GIF
        download_image(video_result.video_url, result_mp4)
        convert_mp4_to_gif(
            result_mp4,
            output,
            fps=output_fps,
            max_width=output_max_width,
        )

    return output
