"""Detect input image aspect ratio and map to dreamina-supported ratios."""
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


def _ratio_to_float(ratio: str) -> float:
    """Convert a 'W:H' string to a float W/H."""
    w, h = ratio.split(":")
    return int(w) / int(h)


_RATIO_FLOATS: tuple[tuple[str, float], ...] = tuple(
    (r, _ratio_to_float(r)) for r in SUPPORTED_RATIOS
)


def detect_ratio(width: int, height: int) -> str:
    """Pick the dreamina-supported ratio whose aspect is closest to width/height.

    Closeness is measured in log-space to be symmetric around 1:1.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"width and height must be positive, got {width}x{height}")

    target = math.log(width / height)
    best = min(
        _RATIO_FLOATS,
        key=lambda rf: abs(math.log(rf[1]) - target),
    )
    return best[0]


def ratio_for_image(image_path: Path) -> str:
    """Open an image file and return the closest dreamina-supported ratio."""
    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"image not found: {image_path}")
    with Image.open(image_path) as img:
        width, height = img.size
    return detect_ratio(width, height)
