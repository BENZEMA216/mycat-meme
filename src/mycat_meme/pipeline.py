"""High-level pipeline that orchestrates ratio detection, prompt selection,
dreamina invocation, JSON parsing, and downloading the result image.
"""
from __future__ import annotations

from pathlib import Path

from mycat_meme.dreamina import (
    Image2ImageStillPending,
    download_image,
    parse_image2image_result,
    run_image2image,
    wait_for_result,
)
from mycat_meme.prompts import DEFAULT_STYLE, get_prompt
from mycat_meme.ratio import ratio_for_image

# 180s is enough for most cat-replacement runs on the high_aes_general_v50
# queue. If dreamina still isn't done by then, the pipeline falls through
# to wait_for_result which polls query_result for up to 5 more minutes.
DEFAULT_POLL_SECONDS = 180


def replace(
    *,
    meme: Path,
    cat: Path,
    output: Path,
    style: str = DEFAULT_STYLE,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
) -> Path:
    """Replace the cat in `meme` with the cat in `cat`, writing to `output`.

    Args:
        meme: Path to the original cat meme image (PNG/JPG/WEBP).
        cat: Path to the user's cat photo (PNG/JPG/WEBP).
        output: Where to write the result. Parent dirs will be created.
        style: Prompt style key (default "default").
        poll_seconds: Max seconds to wait inline for dreamina to finish.

    Returns:
        The `output` path on success.

    Raises:
        FileNotFoundError: if meme or cat does not exist.
        DreaminaNotInstalled: if the dreamina CLI is not on PATH.
        DreaminaCallFailed: if dreamina returns a non-zero exit code.
        OutputNotFound: if dreamina succeeded but the result could not be
            parsed or downloaded.
    """
    meme = Path(meme)
    cat = Path(cat)
    output = Path(output)

    if not meme.exists():
        raise FileNotFoundError(f"meme image not found: {meme}")
    if not cat.exists():
        raise FileNotFoundError(f"cat image not found: {cat}")

    ratio = ratio_for_image(meme)
    prompt = get_prompt(style)

    stdout = run_image2image(
        meme=meme,
        cat=cat,
        prompt=prompt,
        ratio=ratio,
        poll_seconds=poll_seconds,
    )

    try:
        result = parse_image2image_result(stdout)
    except Image2ImageStillPending as pending:
        # The initial poll timed out before dreamina finished. Fall back to
        # query_result polling for up to 5 more minutes.
        result = wait_for_result(pending.submit_id)

    download_image(result.image_url, output)
    return output
