"""Detect input image/video aspect ratio and map to dreamina-supported ratios.

dreamina image2image and dreamina multimodal2video accept different sets of
aspect ratios, so this module exposes both:

    SUPPORTED_RATIOS       — image2image set (8 ratios, includes 3:2 / 2:3)
    VIDEO_SUPPORTED_RATIOS — multimodal2video set (6 ratios, no 3:2 / 2:3)

`detect_ratio(width, height, supported=...)` accepts either set.
"""
import math
from pathlib import Path

from PIL import Image

SUPPORTED_RATIOS: tuple[str, ...] = (
    "21:9",
    "16:9",
    "3:2",
    "4:3",
    "1:1",
    "3:4",
    "2:3",
    "9:16",
)

# multimodal2video accepts a smaller set — no 3:2 or 2:3.
VIDEO_SUPPORTED_RATIOS: tuple[str, ...] = (
    "1:1",
    "3:4",
    "16:9",
    "4:3",
    "9:16",
    "21:9",
)


def _ratio_to_float(ratio: str) -> float:
    """Convert a 'W:H' string to a float W/H."""
    w, h = ratio.split(":")
    return int(w) / int(h)


def _ratio_floats(supported: tuple[str, ...]) -> tuple[tuple[str, float], ...]:
    return tuple((r, _ratio_to_float(r)) for r in supported)


def detect_ratio(
    width: int,
    height: int,
    supported: tuple[str, ...] = SUPPORTED_RATIOS,
) -> str:
    """Pick the supported ratio whose aspect is closest to width/height.

    Closeness is measured in log-space to be symmetric around 1:1, so a
    1100x1000 image and a 1000x1100 image map to their respective near-square
    ratios with equal accuracy.

    Args:
        width, height: positive pixel dimensions.
        supported: tuple of "W:H" strings to choose from. Defaults to the
            image2image set; pass VIDEO_SUPPORTED_RATIOS for video commands.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"width and height must be positive, got {width}x{height}")
    if not supported:
        raise ValueError("supported ratio list must not be empty")

    target = math.log(width / height)
    best = min(
        _ratio_floats(supported),
        key=lambda rf: abs(math.log(rf[1]) - target),
    )
    return best[0]


def ratio_for_image(
    image_path: Path,
    supported: tuple[str, ...] = SUPPORTED_RATIOS,
) -> str:
    """Open an image file and return the closest supported ratio."""
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"image not found: {image_path}")
    with Image.open(image_path) as img:
        width, height = img.size
    return detect_ratio(width, height, supported=supported)
