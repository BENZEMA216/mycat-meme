"""Tests for the dreamina subprocess wrapper.

These tests mock subprocess.run so they don't hit the real dreamina CLI.
"""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mycat_meme.dreamina import (
    DREAMINA_BINARY,
    build_image2image_argv,
    locate_output_image,
    run_image2image,
)
from mycat_meme.errors import (
    DreaminaCallFailed,
    DreaminaNotInstalled,
    OutputNotFound,
)


# ---------- build_image2image_argv ----------

def test_build_image2image_argv_basic(tmp_path: Path):
    meme = tmp_path / "meme.png"
    cat = tmp_path / "cat.jpg"
    meme.touch()
    cat.touch()

    argv = build_image2image_argv(
        meme=meme,
        cat=cat,
        prompt="test prompt",
        ratio="1:1",
        poll_seconds=60,
    )

    assert argv[0] == DREAMINA_BINARY
    assert "image2image" in argv
    images_idx = argv.index("--images")
    assert argv[images_idx + 1] == str(meme)
    assert argv[images_idx + 2] == str(cat)
    prompt_idx = argv.index("--prompt")
    assert argv[prompt_idx + 1] == "test prompt"
    ratio_idx = argv.index("--ratio")
    assert argv[ratio_idx + 1] == "1:1"
    poll_idx = argv.index("--poll")
    assert argv[poll_idx + 1] == "60"


def test_build_image2image_argv_uses_absolute_paths(tmp_path: Path):
    meme = tmp_path / "meme.png"
    cat = tmp_path / "cat.jpg"
    meme.touch()
    cat.touch()

    argv = build_image2image_argv(
        meme=meme, cat=cat, prompt="x", ratio="1:1", poll_seconds=60
    )
    images_idx = argv.index("--images")
    assert Path(argv[images_idx + 1]).is_absolute()
    assert Path(argv[images_idx + 2]).is_absolute()


# ---------- run_image2image ----------

def test_run_image2image_raises_when_dreamina_not_installed(monkeypatch, tmp_path):
    """If subprocess.run raises FileNotFoundError, we re-raise as DreaminaNotInstalled."""
    def fake_run(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory: 'dreamina'")

    monkeypatch.setattr(subprocess, "run", fake_run)

    meme = tmp_path / "m.png"
    cat = tmp_path / "c.jpg"
    meme.touch()
    cat.touch()

    with pytest.raises(DreaminaNotInstalled):
        run_image2image(meme=meme, cat=cat, prompt="x", ratio="1:1", poll_seconds=60)


def test_run_image2image_raises_on_nonzero_exit(monkeypatch, tmp_path):
    fake_result = MagicMock(returncode=1, stdout="", stderr="auth required")

    def fake_run(*args, **kwargs):
        return fake_result

    monkeypatch.setattr(subprocess, "run", fake_run)

    meme = tmp_path / "m.png"
    cat = tmp_path / "c.jpg"
    meme.touch()
    cat.touch()

    with pytest.raises(DreaminaCallFailed) as exc_info:
        run_image2image(meme=meme, cat=cat, prompt="x", ratio="1:1", poll_seconds=60)

    assert exc_info.value.returncode == 1
    assert "auth required" in exc_info.value.stderr


def test_run_image2image_returns_stdout_on_success(monkeypatch, tmp_path):
    """On success, run_image2image returns the raw stdout for downstream parsing."""
    fake_stdout = "submit_id=abc123\nresult: /tmp/out.png\n"
    fake_result = MagicMock(returncode=0, stdout=fake_stdout, stderr="")

    def fake_run(*args, **kwargs):
        return fake_result

    monkeypatch.setattr(subprocess, "run", fake_run)

    meme = tmp_path / "m.png"
    cat = tmp_path / "c.jpg"
    meme.touch()
    cat.touch()

    stdout = run_image2image(meme=meme, cat=cat, prompt="x", ratio="1:1", poll_seconds=60)
    assert stdout == fake_stdout


# ---------- locate_output_image ----------

def test_locate_output_image_finds_path_in_stdout(tmp_path: Path):
    """locate_output_image extracts the most recent image path from dreamina stdout."""
    out_file = tmp_path / "out.png"
    out_file.write_bytes(b"fake png")

    stdout = f"task done\nsaved to: {out_file}\nbye"
    located = locate_output_image(stdout, search_dirs=[tmp_path])
    assert located == out_file


def test_locate_output_image_falls_back_to_search_dir(tmp_path: Path):
    """If stdout doesn't contain a path, look in search_dirs for the newest image."""
    out_file = tmp_path / "newest.png"
    out_file.write_bytes(b"fake")

    stdout = "submit_id=abc\nstatus=done\n"
    located = locate_output_image(stdout, search_dirs=[tmp_path])
    assert located == out_file


def test_locate_output_image_raises_when_nothing_found(tmp_path: Path):
    stdout = "no useful info"
    with pytest.raises(OutputNotFound):
        locate_output_image(stdout, search_dirs=[tmp_path])
