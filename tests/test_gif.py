"""Tests for the ffmpeg/ffprobe wrapper module.

Most tests mock subprocess.run so they don't require ffmpeg installed in CI.
A small "real ffmpeg" smoke test runs at the end if ffmpeg is on PATH.
"""
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mycat_meme.errors import FfmpegFailed, FfmpegNotInstalled
from mycat_meme.gif import (
    FFMPEG_BINARY,
    FFPROBE_BINARY,
    VideoMetadata,
    convert_mp4_to_gif,
    convert_to_mp4,
    ensure_ffmpeg_available,
    extract_first_frame,
    probe_video,
)


def _ok(stdout: str = ""):
    return MagicMock(returncode=0, stdout=stdout, stderr="")


def _fail(returncode: int = 1, stderr: str = "boom"):
    return MagicMock(returncode=returncode, stdout="", stderr=stderr)


# ---------- extract_first_frame ----------

def test_extract_first_frame_builds_correct_argv(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = tmp_path / "in.gif"
    dest = tmp_path / "out" / "first.png"
    src.touch()

    result = extract_first_frame(src, dest)

    assert result == dest
    argv = captured["argv"]
    assert argv[0] == FFMPEG_BINARY
    assert "-y" in argv
    assert "-i" in argv
    assert str(src) in argv
    assert "-vframes" in argv
    assert "1" in argv
    assert str(dest) in argv
    assert dest.parent.exists()  # parent dirs created


def test_extract_first_frame_raises_when_ffmpeg_missing(monkeypatch, tmp_path):
    def boom(*a, **kw):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(subprocess, "run", boom)

    with pytest.raises(FfmpegNotInstalled):
        extract_first_frame(tmp_path / "in.gif", tmp_path / "out.png")


def test_extract_first_frame_raises_on_nonzero_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _fail(stderr="bad input"))

    with pytest.raises(FfmpegFailed) as exc_info:
        extract_first_frame(tmp_path / "in.gif", tmp_path / "out.png")
    assert "bad input" in exc_info.value.stderr


# ---------- convert_to_mp4 ----------

def test_convert_to_mp4_uses_h264_yuv420(monkeypatch, tmp_path: Path):
    """convert_to_mp4 calls ffprobe first to get source dimensions, then ffmpeg
    with the computed safe target dimensions."""
    captured: list = []
    probe_stdout = json.dumps(
        {
            "streams": [{"width": 240, "height": 230}],
            "format": {"duration": "3.0"},
        }
    )

    def fake_run(argv, **kwargs):
        captured.append(argv)
        if argv[0] == FFPROBE_BINARY:
            return _ok(stdout=probe_stdout)
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = tmp_path / "in.gif"
    dest = tmp_path / "out.mp4"
    src.touch()

    convert_to_mp4(src, dest)

    # First call should be ffprobe, second should be ffmpeg
    assert captured[0][0] == FFPROBE_BINARY
    ffmpeg_argv = captured[1]
    assert FFMPEG_BINARY in ffmpeg_argv
    assert "libx264" in ffmpeg_argv
    assert "yuv420p" in ffmpeg_argv
    assert str(src) in ffmpeg_argv
    assert str(dest) in ffmpeg_argv
    # FPS forced to 30
    assert "-r" in ffmpeg_argv
    assert ffmpeg_argv[ffmpeg_argv.index("-r") + 1] == "30"
    # scale filter present with explicit width:height (not just trunc)
    vf_idx = ffmpeg_argv.index("-vf")
    vf = ffmpeg_argv[vf_idx + 1]
    assert "scale=" in vf
    assert "lanczos" in vf


def test_dreamina_safe_video_dimensions_upscales_small_input():
    """A 240x230 input must be upscaled so total pixels >= 409600 and edges >= 320."""
    from mycat_meme.gif import (
        DREAMINA_VIDEO_MAX_PIXELS,
        DREAMINA_VIDEO_MIN_EDGE,
        DREAMINA_VIDEO_MIN_PIXELS,
        _dreamina_safe_video_dimensions,
    )
    w, h = _dreamina_safe_video_dimensions(240, 230)
    assert w * h >= DREAMINA_VIDEO_MIN_PIXELS
    assert w * h <= DREAMINA_VIDEO_MAX_PIXELS
    assert min(w, h) >= DREAMINA_VIDEO_MIN_EDGE
    # Aspect ratio approximately preserved
    assert abs((w / h) - (240 / 230)) < 0.1
    # Even dimensions
    assert w % 2 == 0
    assert h % 2 == 0


def test_dreamina_safe_image_dimensions_downscales_tall_portrait():
    """A 1280x2276 portrait must be clamped within image envelope."""
    from mycat_meme.gif import (
        DREAMINA_IMAGE_MAX_ASPECT,
        DREAMINA_IMAGE_MAX_EDGE,
        DREAMINA_IMAGE_MIN_EDGE,
        _dreamina_safe_image_dimensions,
    )
    w, h = _dreamina_safe_image_dimensions(1280, 2276)
    assert min(w, h) >= DREAMINA_IMAGE_MIN_EDGE
    assert max(w, h) <= DREAMINA_IMAGE_MAX_EDGE
    # The original 1280/2276 = 0.562 is within [0.4, 2.5], so the aspect should
    # round-trip without clamping
    assert abs((w / h) - (1280 / 2276)) < 0.05


def test_dreamina_safe_image_dimensions_clamps_extreme_aspect():
    """An aspect outside [0.4, 2.5] gets clamped."""
    from mycat_meme.gif import (
        DREAMINA_IMAGE_MAX_ASPECT,
        DREAMINA_IMAGE_MIN_ASPECT,
        _dreamina_safe_image_dimensions,
    )
    # 100x1000 is aspect 0.1, well below the 0.4 minimum
    w, h = _dreamina_safe_image_dimensions(100, 1000)
    # Allow ~0.5% drift from rounding to even pixel dimensions
    assert (w / h) >= DREAMINA_IMAGE_MIN_ASPECT - 0.005
    assert (w / h) <= DREAMINA_IMAGE_MAX_ASPECT + 0.005


# ---------- convert_mp4_to_gif ----------

def test_convert_mp4_to_gif_uses_palette_filter(monkeypatch, tmp_path: Path):
    captured = {}

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: (captured.setdefault("argv", argv), _ok())[1],
    )

    src = tmp_path / "in.mp4"
    dest = tmp_path / "out.gif"
    src.touch()

    convert_mp4_to_gif(src, dest, fps=12, max_width=480)

    argv = captured["argv"]
    assert "-vf" in argv
    vf_idx = argv.index("-vf")
    vf_value = argv[vf_idx + 1]
    assert "fps=12" in vf_value
    assert "scale=480" in vf_value
    assert "palettegen" in vf_value
    assert "paletteuse" in vf_value


# ---------- probe_video ----------

def test_probe_video_parses_ffprobe_json(monkeypatch, tmp_path: Path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")

    fake_stdout = json.dumps(
        {
            "streams": [{"width": 1280, "height": 720}],
            "format": {"duration": "5.042"},
        }
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _ok(stdout=fake_stdout))

    meta = probe_video(src)
    assert isinstance(meta, VideoMetadata)
    assert meta.width == 1280
    assert meta.height == 720
    assert meta.duration_seconds == pytest.approx(5.042)


def test_probe_video_raises_on_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        probe_video(tmp_path / "no.mp4")


def test_probe_video_raises_on_no_streams(monkeypatch, tmp_path: Path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: _ok(stdout=json.dumps({"streams": [], "format": {}})),
    )
    with pytest.raises(FfmpegFailed):
        probe_video(src)


def test_probe_video_uses_ffprobe_binary(monkeypatch, tmp_path: Path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return _ok(stdout=json.dumps({"streams": [{"width": 1, "height": 1}], "format": {"duration": "1"}}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    probe_video(src)
    assert captured["argv"][0] == FFPROBE_BINARY


# ---------- ensure_ffmpeg_available ----------

def test_ensure_ffmpeg_available_passes_when_present(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/{name}")
    ensure_ffmpeg_available()  # should not raise


def test_ensure_ffmpeg_available_raises_when_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(FfmpegNotInstalled):
        ensure_ffmpeg_available()


# ---------- real ffmpeg smoke (skip if not installed) ----------

@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)
def test_real_ffmpeg_round_trip(tmp_path: Path):
    """Generate a tiny mp4 with ffmpeg, then probe it and extract a frame."""
    # Use ffmpeg's lavfi testsrc to make a 1-second 64x48 mp4
    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "testsrc=duration=1:size=64x48:rate=10",
            "-pix_fmt", "yuv420p",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    assert src.exists()

    meta = probe_video(src)
    assert meta.width == 64
    assert meta.height == 48
    assert meta.duration_seconds == pytest.approx(1.0, abs=0.1)

    frame = extract_first_frame(src, tmp_path / "first.png")
    assert frame.exists()
    assert frame.stat().st_size > 0
