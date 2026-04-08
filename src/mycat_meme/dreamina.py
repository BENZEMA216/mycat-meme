"""Subprocess wrapper for the `dreamina` CLI's generation commands.

Public surface (image2image):
    DREAMINA_BINARY            — name of the binary to invoke (overridable)
    Image2ImageResult          — dataclass for parsed image2image output
    Image2ImageStillPending    — raised when dreamina hasn't finished yet
    build_image2image_argv(...)
    run_image2image(...)
    parse_image2image_result(stdout)
    wait_for_result(submit_id, ...)
    download_image(url, dest)

Public surface (multimodal2video — v0.2):
    VideoResult                — dataclass for parsed video output
    build_multimodal2video_argv(...)
    run_multimodal2video(...)
    parse_video_result(stdout)
    wait_for_video_result(submit_id, ...)

Shared:
    run_query_result(submit_id) — generic query_result subprocess wrapper

See notes/dreamina-image2image-probe.md for the empirical contract this module
implements.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from mycat_meme.errors import (
    DreaminaCallFailed,
    DreaminaNotInstalled,
    MycatMemeError,
    OutputNotFound,
)

DREAMINA_BINARY = "dreamina"

# Substrings indicating a transient/retryable dreamina backend error.
# These come from dreamina's own Go errors when its backend HTTP layer
# drops connections. Used by the polling loops below to swallow blips.
_TRANSIENT_ERROR_MARKERS = (
    "context deadline exceeded",
    "connection reset",
    "i/o timeout",
    "no such host",
    "tls handshake",
    "eof",
    "broken pipe",
    "temporarily unavailable",
)


def _is_transient_error(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _TRANSIENT_ERROR_MARKERS)


class Image2ImageStillPending(MycatMemeError):
    """Raised by parse_image2image_result when dreamina returned a non-final
    status like 'querying' (the task is still running on the backend).

    Carries the submit_id so callers can poll query_result.
    """

    def __init__(self, submit_id: str, gen_status: str):
        self.submit_id = submit_id
        self.gen_status = gen_status
        super().__init__(
            f"dreamina image2image still pending (submit_id={submit_id!r}, "
            f"gen_status={gen_status!r}); call query_result to wait for completion"
        )

# User-Agent for downloading the result image. The dreamina signed URLs sit
# behind a CDN that rejects requests with no UA in some regions.
_DOWNLOAD_USER_AGENT = "mycat-meme/0.1 (+https://github.com/BENZEMA216/mycat-meme)"


@dataclass(frozen=True)
class Image2ImageResult:
    """Parsed result of a successful `dreamina image2image` call."""

    submit_id: str
    image_url: str
    width: int
    height: int


@dataclass(frozen=True)
class VideoResult:
    """Parsed result of a successful video-generating dreamina call
    (image2video, multimodal2video, etc.)."""

    submit_id: str
    video_url: str
    width: int
    height: int
    fps: float
    duration_seconds: float
    format: str  # typically "mp4"


def build_image2image_argv(
    *,
    meme: Path,
    cat: Path,
    prompt: str,
    ratio: str,
    poll_seconds: int,
) -> list[str]:
    """Build the argv for `dreamina image2image`.

    Multiple input images are passed as a single comma-separated value to the
    `--images` flag — `dreamina image2image --images A B` (space-separated)
    silently fails with exit 1, while `--images A,B` works. See
    notes/dreamina-image2image-probe.md.

    Both image paths are converted to absolute paths so that subprocess
    invocation is independent of the current working directory.
    """
    images_csv = ",".join(
        [str(Path(meme).resolve()), str(Path(cat).resolve())]
    )
    return [
        DREAMINA_BINARY,
        "image2image",
        "--images",
        images_csv,
        "--prompt",
        prompt,
        "--ratio",
        ratio,
        "--poll",
        str(poll_seconds),
    ]


def run_image2image(
    *,
    meme: Path,
    cat: Path,
    prompt: str,
    ratio: str,
    poll_seconds: int,
) -> str:
    """Run `dreamina image2image` and return its stdout on success.

    Raises:
        DreaminaNotInstalled: if the dreamina binary is not on PATH.
        DreaminaCallFailed: if dreamina exits with a non-zero code.
    """
    argv = build_image2image_argv(
        meme=meme, cat=cat, prompt=prompt, ratio=ratio, poll_seconds=poll_seconds
    )
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise DreaminaNotInstalled(
            f"dreamina CLI not found on PATH (looked for {DREAMINA_BINARY!r})"
        ) from e

    if result.returncode != 0:
        raise DreaminaCallFailed(returncode=result.returncode, stderr=result.stderr)

    return result.stdout


_PENDING_STATUSES = frozenset({"querying", "pending", "queueing", "processing"})


def parse_image2image_result(stdout: str) -> Image2ImageResult:
    """Parse the JSON `dreamina image2image` writes to stdout.

    Expected shape (see notes/dreamina-image2image-probe.md):
        {
            "submit_id": "...",
            "gen_status": "success",
            "result_json": {
                "images": [{"image_url": "https://...", "width": N, "height": N}],
                "videos": []
            },
            ...
        }

    Raises:
        OutputNotFound: if stdout is not valid JSON, gen_status indicates
            failure, or there is no image in result_json.images.
        Image2ImageStillPending: if gen_status indicates the task is still
            running (poll timeout was hit before completion). The exception
            carries the submit_id so the caller can call wait_for_result.
    """
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise OutputNotFound(
            f"dreamina stdout was not valid JSON: {e}\n--- stdout was: ---\n{stdout[:500]}"
        ) from e

    gen_status = data.get("gen_status")
    submit_id = data.get("submit_id", "")

    if gen_status in _PENDING_STATUSES:
        raise Image2ImageStillPending(submit_id=submit_id, gen_status=gen_status)

    if gen_status != "success":
        raise OutputNotFound(
            f"dreamina gen_status was {gen_status!r} (expected 'success'); "
            f"full response: {stdout[:500]}"
        )

    images = (data.get("result_json") or {}).get("images") or []
    if not images:
        raise OutputNotFound(
            f"dreamina returned no images in result_json.images; "
            f"full response: {stdout[:500]}"
        )

    first = images[0]
    image_url = first.get("image_url")
    if not image_url:
        raise OutputNotFound(
            f"dreamina image entry has no image_url; full response: {stdout[:500]}"
        )

    return Image2ImageResult(
        submit_id=submit_id,
        image_url=image_url,
        width=int(first.get("width", 0)),
        height=int(first.get("height", 0)),
    )


def parse_video_result(stdout: str) -> VideoResult:
    """Parse the JSON written by dreamina video commands (image2video,
    multimodal2video, etc.).

    Expected shape:
        {
            "submit_id": "...",
            "gen_status": "success",
            "result_json": {
                "images": [],
                "videos": [{
                    "video_url": "https://...",
                    "fps": 24,
                    "width": 1112,
                    "height": 834,
                    "format": "mp4",
                    "duration": 5.042
                }]
            },
            ...
        }

    Raises:
        OutputNotFound: if the JSON is malformed, status is failure, or no
            videos are present.
        Image2ImageStillPending: if status is a pending state. (Same exception
            class as for image2image — the submit_id semantics are identical.)
    """
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise OutputNotFound(
            f"dreamina stdout was not valid JSON: {e}\n--- stdout was: ---\n{stdout[:500]}"
        ) from e

    gen_status = data.get("gen_status")
    submit_id = data.get("submit_id", "")

    if gen_status in _PENDING_STATUSES:
        raise Image2ImageStillPending(submit_id=submit_id, gen_status=gen_status)

    if gen_status != "success":
        fail_reason = data.get("fail_reason", "")
        raise OutputNotFound(
            f"dreamina gen_status was {gen_status!r} (expected 'success'); "
            f"fail_reason: {fail_reason}; full response: {stdout[:500]}"
        )

    videos = (data.get("result_json") or {}).get("videos") or []
    if not videos:
        raise OutputNotFound(
            f"dreamina returned no videos in result_json.videos; "
            f"full response: {stdout[:500]}"
        )

    first = videos[0]
    video_url = first.get("video_url")
    if not video_url:
        raise OutputNotFound(
            f"dreamina video entry has no video_url; full response: {stdout[:500]}"
        )

    return VideoResult(
        submit_id=submit_id,
        video_url=video_url,
        width=int(first.get("width", 0)),
        height=int(first.get("height", 0)),
        fps=float(first.get("fps", 0) or 0),
        duration_seconds=float(first.get("duration", 0) or 0),
        format=str(first.get("format", "mp4")),
    )


def build_multimodal2video_argv(
    *,
    images: list[Path],
    videos: list[Path] | None = None,
    prompt: str,
    duration: int,
    ratio: str,
    model_version: str = "seedance2.0fast",
    video_resolution: str = "720p",
    poll_seconds: int = 240,
) -> list[str]:
    """Build argv for `dreamina multimodal2video`.

    Unlike image2image (which takes comma-joined `--images`), multimodal2video
    uses **repeated** `--image` and `--video` flags (cobra stringArray) — pass
    each path as a separate flag occurrence.

    Args:
        images: One or more local image paths. Up to 9 are accepted by dreamina.
        videos: Zero or more local video paths. Up to 3 accepted.
        prompt: Multimodal generation prompt.
        duration: Output length in seconds (4-15).
        ratio: From VIDEO_SUPPORTED_RATIOS.
        model_version: One of the seedance2.0 family.
    """
    if not images:
        raise ValueError("multimodal2video requires at least one image")
    videos = list(videos or [])

    argv = [DREAMINA_BINARY, "multimodal2video"]
    for img in images:
        argv += ["--image", str(Path(img).resolve())]
    for vid in videos:
        argv += ["--video", str(Path(vid).resolve())]
    argv += [
        "--prompt", prompt,
        "--duration", str(duration),
        "--ratio", ratio,
        "--video_resolution", video_resolution,
        "--model_version", model_version,
        "--poll", str(poll_seconds),
    ]
    return argv


def run_multimodal2video(
    *,
    images: list[Path],
    videos: list[Path] | None = None,
    prompt: str,
    duration: int,
    ratio: str,
    model_version: str = "seedance2.0fast",
    video_resolution: str = "720p",
    poll_seconds: int = 240,
) -> str:
    """Run `dreamina multimodal2video` and return its stdout JSON.

    Raises:
        DreaminaNotInstalled: if dreamina is not on PATH.
        DreaminaCallFailed: on non-zero exit.
    """
    argv = build_multimodal2video_argv(
        images=images,
        videos=videos,
        prompt=prompt,
        duration=duration,
        ratio=ratio,
        model_version=model_version,
        video_resolution=video_resolution,
        poll_seconds=poll_seconds,
    )
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as e:
        raise DreaminaNotInstalled(
            f"dreamina CLI not found on PATH (looked for {DREAMINA_BINARY!r})"
        ) from e
    if result.returncode != 0:
        raise DreaminaCallFailed(returncode=result.returncode, stderr=result.stderr)
    return result.stdout


def wait_for_video_result(
    submit_id: str,
    *,
    max_wait_seconds: int = 600,
    poll_interval_seconds: float = 5.0,
) -> VideoResult:
    """Like wait_for_result, but parses the result as a video.

    Used after multimodal2video / image2video when the initial poll times out.
    Swallows transient query_result network errors and keeps polling.
    """
    deadline = time.monotonic() + max_wait_seconds
    last_stdout = ""
    while True:
        try:
            last_stdout = run_query_result(submit_id)
            return parse_video_result(last_stdout)
        except Image2ImageStillPending:
            pass
        except DreaminaCallFailed as e:
            if not _is_transient_error(e.stderr):
                raise
            # transient: just keep polling
        if time.monotonic() >= deadline:
            raise OutputNotFound(
                f"dreamina video task {submit_id!r} did not finish within "
                f"{max_wait_seconds}s; last stdout: {last_stdout[:500]}"
            )
        time.sleep(poll_interval_seconds)


def run_query_result(submit_id: str) -> str:
    """Call `dreamina query_result --submit_id=<id>` and return stdout.

    Raises:
        DreaminaNotInstalled: if dreamina is not on PATH.
        DreaminaCallFailed: on non-zero exit.
    """
    argv = [DREAMINA_BINARY, "query_result", "--submit_id", submit_id]
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as e:
        raise DreaminaNotInstalled(
            f"dreamina CLI not found on PATH (looked for {DREAMINA_BINARY!r})"
        ) from e
    if result.returncode != 0:
        raise DreaminaCallFailed(returncode=result.returncode, stderr=result.stderr)
    return result.stdout


def wait_for_result(
    submit_id: str,
    *,
    max_wait_seconds: int = 300,
    poll_interval_seconds: float = 5.0,
) -> Image2ImageResult:
    """Poll `dreamina query_result` until the task succeeds or times out.

    Swallows transient query_result network errors and keeps polling.

    Args:
        submit_id: The submit_id returned from a still-pending image2image call.
        max_wait_seconds: Hard upper bound on total wait time across polls.
        poll_interval_seconds: Sleep between query_result calls.

    Returns:
        The parsed Image2ImageResult once gen_status is 'success'.

    Raises:
        OutputNotFound: if the task fails or times out.
        DreaminaCallFailed: on non-transient query_result errors.
        DreaminaNotInstalled: from underlying subprocess.
    """
    deadline = time.monotonic() + max_wait_seconds
    last_stdout = ""
    while True:
        try:
            last_stdout = run_query_result(submit_id)
            return parse_image2image_result(last_stdout)
        except Image2ImageStillPending:
            pass  # keep waiting
        except DreaminaCallFailed as e:
            if not _is_transient_error(e.stderr):
                raise
            # transient: keep polling
        if time.monotonic() >= deadline:
            raise OutputNotFound(
                f"dreamina task {submit_id!r} did not finish within "
                f"{max_wait_seconds}s; last stdout: {last_stdout[:500]}"
            )
        time.sleep(poll_interval_seconds)


_DOWNLOAD_MAX_ATTEMPTS = 4
_DOWNLOAD_BACKOFF_SECONDS = 2.0


def download_image(url: str, dest: Path) -> Path:
    """Download a remote image URL to `dest` on disk. Returns dest on success.

    Retries up to _DOWNLOAD_MAX_ATTEMPTS times on transient network errors
    (SSL EOF, connection reset, timeout). dreamina's CDN occasionally drops
    HTTPS connections — empirically these resolve on retry.

    Raises:
        OutputNotFound: if all attempts fail (network error, expired signature,
            or non-2xx response).
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": _DOWNLOAD_USER_AGENT})

    last_err: Exception | None = None
    for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                if resp.status >= 300:
                    raise OutputNotFound(
                        f"download of {url!r} returned HTTP {resp.status}"
                    )
                data = resp.read()
            dest.write_bytes(data)
            return dest
        except (urllib.error.URLError, OSError) as e:
            last_err = e
            if attempt == _DOWNLOAD_MAX_ATTEMPTS:
                break
            time.sleep(_DOWNLOAD_BACKOFF_SECONDS * attempt)

    raise OutputNotFound(
        f"failed to download dreamina result from {url!r} after "
        f"{_DOWNLOAD_MAX_ATTEMPTS} attempts: {last_err}"
    ) from last_err
