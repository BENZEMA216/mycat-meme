"""Subprocess wrapper for the `dreamina` CLI's image2image command.

Public surface:
    DREAMINA_BINARY      — name of the binary to invoke (overridable for tests)
    build_image2image_argv(...) — pure function returning the argv list
    run_image2image(...)        — runs subprocess, returns stdout, raises on failure
    locate_output_image(...)    — parses stdout (and falls back to scanning) to find
                                  the path of the generated image on disk
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterable

from mycat_meme.errors import (
    DreaminaCallFailed,
    DreaminaNotInstalled,
    OutputNotFound,
)

DREAMINA_BINARY = "dreamina"

# TODO: verify regex against real dreamina image2image stdout once probe is run
# (see notes/dreamina-image2image-probe.md). Currently assumes dreamina prints an
# absolute path ending in .png/.jpg/.jpeg/.webp somewhere in stdout.
_PATH_LIKE_RE = re.compile(
    r"(?P<path>(?:/[^\s\"']+)+\.(?:png|jpg|jpeg|webp))",
    re.IGNORECASE,
)

_IMAGE_GLOBS = ("*.png", "*.jpg", "*.jpeg", "*.webp")


def build_image2image_argv(
    *,
    meme: Path,
    cat: Path,
    prompt: str,
    ratio: str,
    poll_seconds: int,
) -> list[str]:
    """Build the argv for `dreamina image2image`.

    Both image paths are converted to absolute paths so that subprocess
    invocation is independent of the current working directory.
    """
    return [
        DREAMINA_BINARY,
        "image2image",
        "--images",
        str(Path(meme).resolve()),
        str(Path(cat).resolve()),
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


def locate_output_image(
    stdout: str,
    search_dirs: Iterable[Path],
) -> Path:
    """Find the path of the image dreamina just produced.

    Strategy:
    1. Scan stdout for any absolute path ending in .png/.jpg/.jpeg/.webp.
       Return the LAST one found that exists on disk (dreamina typically
       prints the result path near the end of stdout).
    2. Fall back to scanning search_dirs for the newest image file (by mtime).
    3. Raise OutputNotFound if neither strategy yields anything.

    Args:
        stdout: Captured stdout from `dreamina image2image`.
        search_dirs: Directories to scan as a fallback (e.g. dreamina's runs/ dir).
    """
    # Strategy 1: stdout regex
    matches = list(_PATH_LIKE_RE.finditer(stdout))
    for m in reversed(matches):
        candidate = Path(m.group("path"))
        if candidate.exists():
            return candidate

    # Strategy 2: newest file in search dirs
    candidates: list[Path] = []
    for d in search_dirs:
        d = Path(d)
        if not d.exists():
            continue
        for pattern in _IMAGE_GLOBS:
            candidates.extend(d.rglob(pattern))
    if candidates:
        newest = max(candidates, key=lambda p: p.stat().st_mtime)
        return newest

    raise OutputNotFound(
        "could not locate dreamina output image; "
        "stdout did not contain a path and search_dirs were empty"
    )
