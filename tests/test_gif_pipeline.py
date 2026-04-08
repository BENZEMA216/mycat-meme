"""Tests for the high-level GIF replacement pipeline (v0.2.1).

The v0.2.1 pipeline only makes one dreamina call (multimodal2video with
[first_frame, cat] as the two --image inputs and motion_ref.mp4 as the
--video input). image2image is no longer used.
"""
from pathlib import Path

import pytest

from mycat_meme import gif_pipeline
from mycat_meme.dreamina import Image2ImageStillPending, VideoResult
from mycat_meme.errors import OutputNotFound
from mycat_meme.gif import VideoMetadata


@pytest.fixture
def fake_gif(tmp_path: Path) -> Path:
    p = tmp_path / "input.gif"
    p.write_bytes(b"GIF89a fake content")
    return p


@pytest.fixture
def fake_cat(tmp_path: Path) -> Path:
    p = tmp_path / "cat.jpg"
    p.write_bytes(b"\xff\xd8\xff fake jpeg")
    return p


def _stub_pipeline(monkeypatch, captured: dict | None = None):
    """Patch every external dependency of replace_gif so it runs end-to-end
    without ffmpeg, dreamina, or network."""

    def fake_extract(src, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"fake first frame png")
        return Path(dest)

    def fake_convert_to_mp4(src, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"fake mp4")
        return Path(dest)

    def fake_probe(path):
        return VideoMetadata(width=1280, height=720, duration_seconds=5.0)

    def fake_run_mm2v(**kwargs):
        if captured is not None:
            captured["mm2v_kwargs"] = kwargs
        return '{"submit_id": "stub", "gen_status": "success", "result_json": {"images": [], "videos": [{"video_url": "https://x", "fps": 24, "width": 1280, "height": 720, "format": "mp4", "duration": 5}]}}'

    def fake_parse(stdout):
        return VideoResult(
            submit_id="stub",
            video_url="https://example.com/result.mp4",
            width=1280,
            height=720,
            fps=24,
            duration_seconds=5,
            format="mp4",
        )

    def fake_download(url, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"fake mp4 bytes")
        if captured is not None:
            captured["downloaded_url"] = url
        return Path(dest)

    def fake_convert_mp4_to_gif(src, dest, *, fps, max_width):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"GIF89a fake output")
        if captured is not None:
            captured["gif_fps"] = fps
            captured["gif_max_width"] = max_width
        return Path(dest)

    monkeypatch.setattr(gif_pipeline, "extract_first_frame", fake_extract)
    monkeypatch.setattr(gif_pipeline, "convert_to_mp4", fake_convert_to_mp4)
    monkeypatch.setattr(gif_pipeline, "probe_video", fake_probe)
    monkeypatch.setattr(gif_pipeline, "run_multimodal2video", fake_run_mm2v)
    monkeypatch.setattr(gif_pipeline, "parse_video_result", fake_parse)
    monkeypatch.setattr(gif_pipeline, "download_image", fake_download)
    monkeypatch.setattr(gif_pipeline, "convert_mp4_to_gif", fake_convert_mp4_to_gif)


def test_replace_gif_happy_path(monkeypatch, tmp_path, fake_gif, fake_cat):
    captured: dict = {}
    _stub_pipeline(monkeypatch, captured)

    output = tmp_path / "out.gif"
    result = gif_pipeline.replace_gif(gif=fake_gif, cat=fake_cat, output=output)

    assert result == output
    assert output.exists()
    assert output.read_bytes().startswith(b"GIF89a")
    assert captured["downloaded_url"] == "https://example.com/result.mp4"
    assert captured["gif_fps"] == gif_pipeline.DEFAULT_OUTPUT_FPS
    assert captured["gif_max_width"] == gif_pipeline.DEFAULT_OUTPUT_MAX_WIDTH


def test_replace_gif_passes_both_images_to_multimodal2video(monkeypatch, tmp_path, fake_gif, fake_cat):
    """The v0.2.1 pipeline must pass [first_frame, cat] as the images list,
    and motion_ref.mp4 as the videos list."""
    captured: dict = {}
    _stub_pipeline(monkeypatch, captured)

    gif_pipeline.replace_gif(
        gif=fake_gif, cat=fake_cat, output=tmp_path / "out.gif"
    )
    kwargs = captured["mm2v_kwargs"]
    assert "images" in kwargs
    images = kwargs["images"]
    assert len(images) == 2
    # The user's cat must be the SECOND image (the prompt references "第二张")
    assert str(images[1]).endswith("cat.jpg") or str(images[1]) == str(fake_cat)
    # First image is the extracted first frame from the GIF
    assert "first" in str(images[0]).lower()

    assert "videos" in kwargs
    assert len(kwargs["videos"]) == 1


def test_replace_gif_picks_video_supported_ratio(monkeypatch, tmp_path, fake_gif, fake_cat):
    """A 1280x720 input → 16:9, which is in VIDEO_SUPPORTED_RATIOS."""
    captured: dict = {}
    _stub_pipeline(monkeypatch, captured)

    gif_pipeline.replace_gif(
        gif=fake_gif, cat=fake_cat, output=tmp_path / "out.gif"
    )
    assert captured["mm2v_kwargs"]["ratio"] == "16:9"


def test_replace_gif_rounds_duration(monkeypatch, tmp_path, fake_gif, fake_cat):
    """If the input is 5.042s, the duration sent should be 6 (ceil)."""
    captured: dict = {}

    def fake_probe(path):
        return VideoMetadata(width=1280, height=720, duration_seconds=5.042)

    _stub_pipeline(monkeypatch, captured)
    monkeypatch.setattr(gif_pipeline, "probe_video", fake_probe)

    gif_pipeline.replace_gif(
        gif=fake_gif, cat=fake_cat, output=tmp_path / "out.gif"
    )
    assert captured["mm2v_kwargs"]["duration"] == 6


def test_replace_gif_clamps_long_inputs(monkeypatch, tmp_path, fake_gif, fake_cat):
    """A 30s input should clamp to dreamina's max of 15s."""
    captured: dict = {}

    def fake_probe(path):
        return VideoMetadata(width=1280, height=720, duration_seconds=30.0)

    _stub_pipeline(monkeypatch, captured)
    monkeypatch.setattr(gif_pipeline, "probe_video", fake_probe)

    gif_pipeline.replace_gif(
        gif=fake_gif, cat=fake_cat, output=tmp_path / "out.gif"
    )
    assert captured["mm2v_kwargs"]["duration"] == 15


def test_replace_gif_clamps_short_inputs(monkeypatch, tmp_path, fake_gif, fake_cat):
    """A 0.5s input should clamp UP to dreamina's min of 4s."""
    captured: dict = {}

    def fake_probe(path):
        return VideoMetadata(width=1280, height=720, duration_seconds=0.5)

    _stub_pipeline(monkeypatch, captured)
    monkeypatch.setattr(gif_pipeline, "probe_video", fake_probe)

    gif_pipeline.replace_gif(
        gif=fake_gif, cat=fake_cat, output=tmp_path / "out.gif"
    )
    assert captured["mm2v_kwargs"]["duration"] == 4


def test_replace_gif_explicit_duration_overrides(monkeypatch, tmp_path, fake_gif, fake_cat):
    captured: dict = {}
    _stub_pipeline(monkeypatch, captured)
    gif_pipeline.replace_gif(
        gif=fake_gif, cat=fake_cat, output=tmp_path / "out.gif", duration=10
    )
    assert captured["mm2v_kwargs"]["duration"] == 10


def test_replace_gif_passes_poll_zero_to_multimodal2video(monkeypatch, tmp_path, fake_gif, fake_cat):
    """v0.2.1 always passes poll_seconds=0 to multimodal2video so dreamina
    just submits and we poll query_result ourselves (more reliable)."""
    captured: dict = {}
    _stub_pipeline(monkeypatch, captured)
    gif_pipeline.replace_gif(
        gif=fake_gif, cat=fake_cat, output=tmp_path / "out.gif"
    )
    assert captured["mm2v_kwargs"]["poll_seconds"] == 0


def test_replace_gif_raises_if_gif_missing(tmp_path, fake_cat):
    with pytest.raises(FileNotFoundError):
        gif_pipeline.replace_gif(
            gif=tmp_path / "no.gif",
            cat=fake_cat,
            output=tmp_path / "out.gif",
        )


def test_replace_gif_raises_if_cat_missing(tmp_path, fake_gif):
    with pytest.raises(FileNotFoundError):
        gif_pipeline.replace_gif(
            gif=fake_gif,
            cat=tmp_path / "no.jpg",
            output=tmp_path / "out.gif",
        )


def test_replace_gif_falls_back_to_wait_when_pending(monkeypatch, tmp_path, fake_gif, fake_cat):
    """If parse_video_result raises Image2ImageStillPending, the pipeline
    must call wait_for_video_result with the submit_id."""
    _stub_pipeline(monkeypatch)
    waited = {}

    def raise_pending(stdout):
        raise Image2ImageStillPending(submit_id="abc", gen_status="querying")

    monkeypatch.setattr(gif_pipeline, "parse_video_result", raise_pending)

    def fake_wait(submit_id, **kwargs):
        waited["submit_id"] = submit_id
        return VideoResult(
            submit_id=submit_id,
            video_url="https://done.example/video.mp4",
            width=1280,
            height=720,
            fps=24,
            duration_seconds=5,
            format="mp4",
        )

    monkeypatch.setattr(gif_pipeline, "wait_for_video_result", fake_wait)

    output = tmp_path / "out.gif"
    result = gif_pipeline.replace_gif(gif=fake_gif, cat=fake_cat, output=output)
    assert result == output
    assert waited["submit_id"] == "abc"
