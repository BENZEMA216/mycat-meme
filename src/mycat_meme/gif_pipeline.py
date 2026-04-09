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

from PIL import Image

from mycat_meme.dreamina import (
    Image2ImageStillPending,
    download_image,
    parse_video_result,
    run_multimodal2video,
    wait_for_video_result,
)
from mycat_meme.errors import DreaminaCallFailed, OutputNotFound
from mycat_meme.gif import (
    _dreamina_safe_image_dimensions,
    convert_mp4_to_gif,
    convert_to_mp4,
    extract_first_frame,
    probe_video,
)
from mycat_meme.ratio import VIDEO_SUPPORTED_RATIOS, detect_ratio


def _normalize_image_for_dreamina(src: Path, dest: Path) -> Path:
    """Re-save `src` as a JPEG that satisfies dreamina multimodal2video's
    --image input constraints (size, aspect ratio).

    Small images get upscaled with lanczos, big images downscaled. Output is
    always JPEG quality 92.
    """
    with Image.open(src) as img:
        img = img.convert("RGB")
        target_w, target_h = _dreamina_safe_image_dimensions(img.width, img.height)
        if (target_w, target_h) != img.size:
            img = img.resize((target_w, target_h), Image.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, "JPEG", quality=92, optimize=True)
    return dest

# Default seedance model — fastest of the seedance2.0 family. Quality is
# good enough for cat memes and round-trip is well under 6 minutes.
DEFAULT_VIDEO_MODEL = "seedance2.0fast"

# Output GIF tuning. 15 fps + 600px wide = good size/quality balance.
DEFAULT_OUTPUT_FPS = 15
DEFAULT_OUTPUT_MAX_WIDTH = 600

# dreamina enforces 4-15s on multimodal2video duration; longer inputs clip.
MIN_VIDEO_DURATION = 4
MAX_VIDEO_DURATION = 15

# The multimodal video prompt template.
#
# EMPIRICAL RULES (v0.2.2 experimentation, 6 experiments with the same inputs):
#
# 1. Image order matters — the first --image is weighted as the primary
#    appearance reference. Pass the user's cat photo as image 1 and the
#    first frame of the source GIF as image 2 (scene reference only).
#
# 2. Generic prompts are NOT enough. Without explicit cat-specific
#    descriptors (breed, fur length, color, face shape), the model defaults
#    to the motion reference's cat appearance even with "cat first"
#    ordering. Two strong generic prompts were tested and both lost
#    breed-level features on a golden longhair Persian.
#
# 3. Cat-specific descriptors in the prompt DO work. A prompt mentioning
#    "金色长毛猫"、"蓬松长毛"、"圆脸幼态" preserved the kitten's long fur
#    and face shape; the same inputs without those keywords gave a generic
#    orange tabby shorthair.
#
# Therefore the gif_pipeline accepts an optional `description` kwarg that
# the caller (CLI / Python API) can fill in. If provided, it's injected
# into the prompt near the front so the model "primes" on it.
_VIDEO_PROMPT_TEMPLATE = (
    "严禁修改第一张参考图中的猫。{description_clause}"
    "输出视频里的猫必须是第一张图那只猫本体——一模一样的毛色、毛长、"
    "品种、花纹、体型、年龄。仅根据参考视频的表情动作让这只猫做出相应的"
    "面部变化和头部运动。禁止生成任何其他品种或外观的猫。"
    "场景光线参考第二张图。"
)


def _build_video_prompt(description: str | None) -> str:
    """Build the multimodal2video prompt, optionally injecting a cat description."""
    if description and description.strip():
        clause = f"第一张参考图的猫是：{description.strip()}。"
    else:
        clause = ""
    return _VIDEO_PROMPT_TEMPLATE.format(description_clause=clause)

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
    description: str | None = None,
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
        description: Optional short text description of the cat (breed, fur
            length, color, face shape). Injected into the multimodal prompt
            to help the model preserve breed-level features. Strongly
            recommended for non-generic cats (long hair, specific breeds,
            kittens) — empirically the model reverts to generic shorthair
            tabby without this hint.
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
        first_frame_raw = tmpdir / "first_raw.png"
        first_frame = tmpdir / "first.jpg"
        cat_norm = tmpdir / "cat.jpg"
        motion_ref = tmpdir / "motion_ref.mp4"
        result_mp4 = tmpdir / "result.mp4"

        # 1) Extract first frame and convert input to mp4 motion reference.
        # convert_to_mp4 also enforces dreamina's video size/fps constraints.
        extract_first_frame(gif, first_frame_raw)
        convert_to_mp4(gif, motion_ref)

        # 1b) Normalize both image inputs to dreamina's image envelope. Without
        # this, small GIF first frames (e.g. 240x230) and very tall cat photos
        # (e.g. 1280x2276 portrait) cause dreamina to return 'final generation
        # failed' from its internal validator.
        _normalize_image_for_dreamina(first_frame_raw, first_frame)
        _normalize_image_for_dreamina(cat, cat_norm)

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
        # IMPORTANT: cat_norm goes FIRST (primary appearance reference),
        # first_frame goes second (scene/composition reference). v0.2.2
        # empirically verified that the model weights the first image as
        # authoritative for appearance. The prompt is also built per-call
        # so optional cat descriptions get injected.
        #
        # Pass poll_seconds=0 — dreamina's internal long-poll is unreliable;
        # we poll query_result ourselves below for robustness.
        prompt = _build_video_prompt(description)
        stdout = _retry_transient(
            lambda: run_multimodal2video(
                images=[cat_norm, first_frame],
                videos=[motion_ref],
                prompt=prompt,
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
