"""High-level pipeline that orchestrates ratio detection, prompt selection,
dreamina invocation, and copying the result to the user's output path.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from mycat_meme.dreamina import locate_output_image, run_image2image
from mycat_meme.prompts import DEFAULT_STYLE, get_prompt
from mycat_meme.ratio import ratio_for_image

DEFAULT_POLL_SECONDS = 60

# Directories where dreamina is known to drop output artifacts.
# Adjust based on Task 0 probe findings (TODO once probe is run).
_DREAMINA_OUTPUT_SEARCH_DIRS: tuple[Path, ...] = (
    Path.home() / ".dreamina_cli" / "runs",
    Path.cwd(),
)


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
        OutputNotFound: if dreamina succeeded but the result file could not
            be located on disk.
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

    located = locate_output_image(
        stdout=stdout,
        search_dirs=_DREAMINA_OUTPUT_SEARCH_DIRS,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(located, output)
    return output
