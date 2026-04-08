"""Subprocess wrapper for the `dreamina` CLI's image2image command.

Public surface:
    DREAMINA_BINARY      — name of the binary to invoke (overridable for tests)
    Image2ImageResult    — dataclass for parsed dreamina output
    Image2ImageStillPending — raised when dreamina hasn't finished yet
    build_image2image_argv(...) — pure function returning the argv list
    run_image2image(...)        — runs subprocess, returns stdout JSON, raises on failure
    parse_image2image_result(stdout) — parse the JSON, return Image2ImageResult
    run_query_result(submit_id) — call `dreamina query_result --submit_id=...`
    wait_for_result(submit_id, max_wait_seconds, poll_interval_seconds)
                                — poll query_result until success or timeout
    download_image(url, dest)   — download a remote image URL to a local path

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

    Args:
        submit_id: The submit_id returned from a still-pending image2image call.
        max_wait_seconds: Hard upper bound on total wait time across polls.
        poll_interval_seconds: Sleep between query_result calls.

    Returns:
        The parsed Image2ImageResult once gen_status is 'success'.

    Raises:
        OutputNotFound: if the task fails or times out.
        DreaminaCallFailed / DreaminaNotInstalled: from underlying subprocess.
    """
    deadline = time.monotonic() + max_wait_seconds
    while True:
        stdout = run_query_result(submit_id)
        try:
            return parse_image2image_result(stdout)
        except Image2ImageStillPending:
            pass  # keep waiting
        if time.monotonic() >= deadline:
            raise OutputNotFound(
                f"dreamina task {submit_id!r} did not finish within "
                f"{max_wait_seconds}s; last stdout: {stdout[:500]}"
            )
        time.sleep(poll_interval_seconds)


def download_image(url: str, dest: Path) -> Path:
    """Download a remote image URL to `dest` on disk. Returns dest on success.

    Raises:
        OutputNotFound: if the download fails (network error, expired signature,
            or non-2xx response).
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": _DOWNLOAD_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            if resp.status >= 300:
                raise OutputNotFound(
                    f"download of {url!r} returned HTTP {resp.status}"
                )
            data = resp.read()
    except urllib.error.URLError as e:
        raise OutputNotFound(
            f"failed to download dreamina result from {url!r}: {e}"
        ) from e
    dest.write_bytes(data)
    return dest
