"""Tests for the high-level replace() pipeline.

These tests stub out the dreamina subprocess wrapper so they run offline.
"""
import shutil
from pathlib import Path

import pytest
from PIL import Image

from mycat_meme import pipeline
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


@pytest.fixture
def fake_dreamina_output(tmp_path: Path) -> Path:
    """A fake image that pretends to be dreamina's result."""
    p = tmp_path / "dreamina_runs" / "result.png"
    p.parent.mkdir()
    Image.new("RGB", (800, 600), color=(0, 255, 0)).save(p)
    return p


def test_replace_calls_dreamina_with_detected_ratio(
    monkeypatch, tmp_path, fake_meme, fake_cat, fake_dreamina_output
):
    """replace() should detect 4:3 from an 800x600 meme and pass --ratio 4:3."""
    captured = {}

    def fake_run_image2image(*, meme, cat, prompt, ratio, poll_seconds):
        captured["meme"] = meme
        captured["cat"] = cat
        captured["prompt"] = prompt
        captured["ratio"] = ratio
        captured["poll_seconds"] = poll_seconds
        return f"saved to: {fake_dreamina_output}\n"

    def fake_locate(stdout, search_dirs):
        return fake_dreamina_output

    monkeypatch.setattr(pipeline, "run_image2image", fake_run_image2image)
    monkeypatch.setattr(pipeline, "locate_output_image", fake_locate)

    output = tmp_path / "out.png"
    result = pipeline.replace(meme=fake_meme, cat=fake_cat, output=output)

    assert result == output
    assert output.exists()
    assert captured["ratio"] == "4:3"
    assert captured["prompt"]
    assert "第一张" in captured["prompt"]


def test_replace_copies_dreamina_output_to_destination(
    monkeypatch, tmp_path, fake_meme, fake_cat, fake_dreamina_output
):
    monkeypatch.setattr(
        pipeline,
        "run_image2image",
        lambda **kwargs: f"saved to: {fake_dreamina_output}",
    )
    monkeypatch.setattr(
        pipeline,
        "locate_output_image",
        lambda stdout, search_dirs: fake_dreamina_output,
    )

    output = tmp_path / "subdir" / "out.png"
    result = pipeline.replace(meme=fake_meme, cat=fake_cat, output=output)

    assert result == output
    assert output.exists()
    assert not output.is_symlink()
    assert output.read_bytes() == fake_dreamina_output.read_bytes()


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
    monkeypatch, tmp_path, fake_meme, fake_cat, fake_dreamina_output
):
    captured = {}

    def fake_run_image2image(**kwargs):
        captured.update(kwargs)
        return f"saved to: {fake_dreamina_output}"

    monkeypatch.setattr(pipeline, "run_image2image", fake_run_image2image)
    monkeypatch.setattr(
        pipeline, "locate_output_image", lambda *a, **k: fake_dreamina_output
    )

    pipeline.replace(
        meme=fake_meme,
        cat=fake_cat,
        output=tmp_path / "out.png",
        style=DEFAULT_STYLE,
    )

    assert captured["prompt"]


def test_replace_propagates_output_not_found(
    monkeypatch, tmp_path, fake_meme, fake_cat
):
    monkeypatch.setattr(
        pipeline, "run_image2image", lambda **kwargs: "no path here"
    )

    def raise_not_found(stdout, search_dirs):
        raise OutputNotFound("nothing")

    monkeypatch.setattr(pipeline, "locate_output_image", raise_not_found)

    with pytest.raises(OutputNotFound):
        pipeline.replace(
            meme=fake_meme,
            cat=fake_cat,
            output=tmp_path / "out.png",
        )
