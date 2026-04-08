"""Tests for the high-level replace() pipeline.

These tests stub out dreamina subprocess and HTTP download so they run offline.
"""
from pathlib import Path

import pytest
from PIL import Image

from mycat_meme import pipeline
from mycat_meme.dreamina import Image2ImageResult, Image2ImageStillPending
from mycat_meme.errors import OutputNotFound
from mycat_meme.prompts import DEFAULT_STYLE


@pytest.fixture
def fake_meme(tmp_path: Path) -> Path:
    p = tmp_path / "meme.png"
    Image.new("RGB", (800, 600), color=(200, 100, 50)).save(p)
    return p


@pytest.fixture
def fake_cat(tmp_path: Path) -> Path:
    p = tmp_path / "cat.jpg"
    Image.new("RGB", (1024, 1024), color=(50, 100, 200)).save(p)
    return p


def _stub_pipeline(monkeypatch, captured: dict | None = None):
    """Patch run_image2image, parse_image2image_result, and download_image
    so the pipeline runs end-to-end without touching the network."""
    fake_result = Image2ImageResult(
        submit_id="stub-submit-id",
        image_url="https://example.com/fake.png",
        width=800,
        height=600,
    )

    def fake_run(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return '{"gen_status": "success", "result_json": {"images": [{"image_url": "https://example.com/fake.png", "width": 800, "height": 600}]}, "submit_id": "stub-submit-id"}'

    def fake_parse(stdout):
        return fake_result

    def fake_download(url, dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Write a tiny fake PNG so the test can assert the file exists
        Image.new("RGB", (8, 8), color=(0, 255, 0)).save(dest)
        return dest

    monkeypatch.setattr(pipeline, "run_image2image", fake_run)
    monkeypatch.setattr(pipeline, "parse_image2image_result", fake_parse)
    monkeypatch.setattr(pipeline, "download_image", fake_download)


def test_replace_calls_dreamina_with_detected_ratio(
    monkeypatch, tmp_path, fake_meme, fake_cat
):
    """replace() should detect 4:3 from an 800x600 meme and pass --ratio 4:3."""
    captured: dict = {}
    _stub_pipeline(monkeypatch, captured)

    output = tmp_path / "out.png"
    result = pipeline.replace(meme=fake_meme, cat=fake_cat, output=output)

    assert result == output
    assert output.exists()
    assert captured["ratio"] == "4:3"
    assert captured["prompt"]
    assert "第一张" in captured["prompt"]


def test_replace_writes_downloaded_bytes_to_destination(
    monkeypatch, tmp_path, fake_meme, fake_cat
):
    """The pipeline should put the downloaded image at the user-specified path."""
    _stub_pipeline(monkeypatch)
    output = tmp_path / "subdir" / "out.png"
    result = pipeline.replace(meme=fake_meme, cat=fake_cat, output=output)

    assert result == output
    assert output.exists()
    assert output.stat().st_size > 0


def test_replace_raises_if_meme_missing(tmp_path, fake_cat):
    with pytest.raises(FileNotFoundError):
        pipeline.replace(
            meme=tmp_path / "no-meme.png",
            cat=fake_cat,
            output=tmp_path / "out.png",
        )


def test_replace_raises_if_cat_missing(tmp_path, fake_meme):
    with pytest.raises(FileNotFoundError):
        pipeline.replace(
            meme=fake_meme,
            cat=tmp_path / "no-cat.jpg",
            output=tmp_path / "out.png",
        )


def test_replace_accepts_explicit_style(
    monkeypatch, tmp_path, fake_meme, fake_cat
):
    captured: dict = {}
    _stub_pipeline(monkeypatch, captured)

    pipeline.replace(
        meme=fake_meme,
        cat=fake_cat,
        output=tmp_path / "out.png",
        style=DEFAULT_STYLE,
    )

    assert captured["prompt"]


def test_replace_propagates_output_not_found_from_parse(
    monkeypatch, tmp_path, fake_meme, fake_cat
):
    """If parse_image2image_result raises OutputNotFound, the pipeline propagates it."""
    monkeypatch.setattr(pipeline, "run_image2image", lambda **kwargs: "garbage")

    def raise_not_found(stdout):
        raise OutputNotFound("bad json")

    monkeypatch.setattr(pipeline, "parse_image2image_result", raise_not_found)

    with pytest.raises(OutputNotFound):
        pipeline.replace(
            meme=fake_meme,
            cat=fake_cat,
            output=tmp_path / "out.png",
        )


def test_replace_propagates_output_not_found_from_download(
    monkeypatch, tmp_path, fake_meme, fake_cat
):
    """If download_image fails, the pipeline propagates the error."""
    monkeypatch.setattr(pipeline, "run_image2image", lambda **kwargs: '{"x":1}')
    monkeypatch.setattr(
        pipeline,
        "parse_image2image_result",
        lambda stdout: Image2ImageResult(
            submit_id="x", image_url="https://x", width=1, height=1
        ),
    )

    def raise_not_found(url, dest):
        raise OutputNotFound("network down")

    monkeypatch.setattr(pipeline, "download_image", raise_not_found)

    with pytest.raises(OutputNotFound):
        pipeline.replace(
            meme=fake_meme,
            cat=fake_cat,
            output=tmp_path / "out.png",
        )


def test_replace_falls_back_to_wait_for_result_when_pending(
    monkeypatch, tmp_path, fake_meme, fake_cat
):
    """If parse_image2image_result raises Image2ImageStillPending, the pipeline
    must call wait_for_result with the submit_id and use its result."""
    monkeypatch.setattr(
        pipeline, "run_image2image", lambda **kwargs: '{"submit_id": "abc"}'
    )

    def raise_pending(stdout):
        raise Image2ImageStillPending(submit_id="abc", gen_status="querying")

    monkeypatch.setattr(pipeline, "parse_image2image_result", raise_pending)

    waited = {}

    def fake_wait(submit_id):
        waited["submit_id"] = submit_id
        return Image2ImageResult(
            submit_id=submit_id,
            image_url="https://example.com/done.png",
            width=8,
            height=8,
        )

    monkeypatch.setattr(pipeline, "wait_for_result", fake_wait)

    downloaded = {}

    def fake_download(url, dest):
        downloaded["url"] = url
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"fake")
        return Path(dest)

    monkeypatch.setattr(pipeline, "download_image", fake_download)

    output = tmp_path / "out.png"
    result = pipeline.replace(meme=fake_meme, cat=fake_cat, output=output)

    assert result == output
    assert waited["submit_id"] == "abc"
    assert downloaded["url"] == "https://example.com/done.png"
