"""Tests for aspect ratio detection and mapping to dreamina-supported ratios."""
from pathlib import Path

import pytest
from PIL import Image

from mycat_meme.ratio import SUPPORTED_RATIOS, detect_ratio, ratio_for_image


def test_supported_ratios_match_dreamina_spec():
    """The supported ratio list must match what dreamina image2image accepts."""
    assert SUPPORTED_RATIOS == ("21:9", "16:9", "3:2", "4:3", "1:1", "3:4", "2:3", "9:16")


@pytest.mark.parametrize(
    "width,height,expected",
    [
        (1024, 1024, "1:1"),
        (1920, 1080, "16:9"),
        (1080, 1920, "9:16"),
        (800, 600, "4:3"),
        (600, 800, "3:4"),
        (1500, 1000, "3:2"),
        (1000, 1500, "2:3"),
        (2100, 900, "21:9"),
        (1000, 1010, "1:1"),
        (1900, 1100, "16:9"),
    ],
)
def test_detect_ratio_from_dimensions(width, height, expected):
    assert detect_ratio(width, height) == expected


def test_ratio_for_image_reads_from_file(tmp_path: Path):
    img_path = tmp_path / "test.png"
    Image.new("RGB", (1920, 1080), color=(0, 0, 0)).save(img_path)
    assert ratio_for_image(img_path) == "16:9"


def test_ratio_for_image_handles_jpeg(tmp_path: Path):
    img_path = tmp_path / "test.jpg"
    Image.new("RGB", (800, 600), color=(0, 0, 0)).save(img_path)
    assert ratio_for_image(img_path) == "4:3"


def test_ratio_for_image_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        ratio_for_image(tmp_path / "does-not-exist.png")
