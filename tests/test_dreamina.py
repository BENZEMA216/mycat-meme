"""Tests for the dreamina subprocess wrapper.

These tests mock subprocess.run and urllib.request.urlopen so they don't hit
the real dreamina CLI or network.
"""
import json
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mycat_meme import dreamina as dreamina_mod
from mycat_meme.dreamina import (
    DREAMINA_BINARY,
    Image2ImageResult,
    Image2ImageStillPending,
    build_image2image_argv,
    download_image,
    parse_image2image_result,
    run_image2image,
    run_query_result,
    wait_for_result,
)
from mycat_meme.errors import (
    DreaminaCallFailed,
    DreaminaNotInstalled,
    OutputNotFound,
)


# A canonical successful response captured from a real dreamina image2image
# call (see notes/dreamina-image2image-probe.md). Inlined here so the test
# is self-contained and doesn't need to read the notes file.
SAMPLE_SUCCESS_STDOUT = json.dumps(
    {
        "submit_id": "6e5308da6245ccee",
        "prompt": "make it watercolor style",
        "gen_status": "success",
        "result_json": {
            "images": [
                {
                    "image_url": "https://p11-dreamina-sign.byteimg.com/tos-cn-i-tb4s082cfz/abc.png?x-expires=1775638800&x-signature=xyz",
                    "width": 4992,
                    "height": 3328,
                }
            ],
            "videos": [],
        },
        "queue_info": {
            "queue_idx": 0,
            "priority": 6,
            "queue_status": "Finish",
            "queue_length": 0,
        },
    }
)


# ---------- build_image2image_argv ----------

def test_build_image2image_argv_uses_comma_separated_images(tmp_path: Path):
    """Multiple images must be comma-joined into a single --images value.
    Space-separated form silently fails (see probe notes)."""
    meme = tmp_path / "meme.png"
    cat = tmp_path / "cat.jpg"
    meme.touch()
    cat.touch()

    argv = build_image2image_argv(
        meme=meme, cat=cat, prompt="test", ratio="1:1", poll_seconds=60
    )

    assert argv[0] == DREAMINA_BINARY
    assert "image2image" in argv
    images_idx = argv.index("--images")
    images_value = argv[images_idx + 1]
    assert "," in images_value
    parts = images_value.split(",")
    assert len(parts) == 2
    assert parts[0] == str(meme.resolve())
    assert parts[1] == str(cat.resolve())
    # The next token after the images value must be --prompt, NOT a stray path
    assert argv[images_idx + 2] == "--prompt"


def test_build_image2image_argv_passes_other_flags(tmp_path: Path):
    meme = tmp_path / "meme.png"
    cat = tmp_path / "cat.jpg"
    meme.touch()
    cat.touch()

    argv = build_image2image_argv(
        meme=meme, cat=cat, prompt="hello world", ratio="3:2", poll_seconds=90
    )
    assert argv[argv.index("--prompt") + 1] == "hello world"
    assert argv[argv.index("--ratio") + 1] == "3:2"
    assert argv[argv.index("--poll") + 1] == "90"


def test_build_image2image_argv_uses_absolute_paths(tmp_path: Path):
    meme = tmp_path / "meme.png"
    cat = tmp_path / "cat.jpg"
    meme.touch()
    cat.touch()

    argv = build_image2image_argv(
        meme=meme, cat=cat, prompt="x", ratio="1:1", poll_seconds=60
    )
    images_value = argv[argv.index("--images") + 1]
    for part in images_value.split(","):
        assert Path(part).is_absolute()


# ---------- run_image2image ----------

def test_run_image2image_raises_when_dreamina_not_installed(monkeypatch, tmp_path):
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
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

    meme = tmp_path / "m.png"
    cat = tmp_path / "c.jpg"
    meme.touch()
    cat.touch()

    with pytest.raises(DreaminaCallFailed) as exc_info:
        run_image2image(meme=meme, cat=cat, prompt="x", ratio="1:1", poll_seconds=60)

    assert exc_info.value.returncode == 1
    assert "auth required" in exc_info.value.stderr


def test_run_image2image_returns_stdout_on_success(monkeypatch, tmp_path):
    fake_result = MagicMock(returncode=0, stdout=SAMPLE_SUCCESS_STDOUT, stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

    meme = tmp_path / "m.png"
    cat = tmp_path / "c.jpg"
    meme.touch()
    cat.touch()

    stdout = run_image2image(meme=meme, cat=cat, prompt="x", ratio="1:1", poll_seconds=60)
    assert stdout == SAMPLE_SUCCESS_STDOUT


# ---------- parse_image2image_result ----------

def test_parse_image2image_result_extracts_url_width_height():
    result = parse_image2image_result(SAMPLE_SUCCESS_STDOUT)
    assert isinstance(result, Image2ImageResult)
    assert result.submit_id == "6e5308da6245ccee"
    assert result.image_url.startswith("https://")
    assert result.width == 4992
    assert result.height == 3328


def test_parse_image2image_result_raises_on_invalid_json():
    with pytest.raises(OutputNotFound):
        parse_image2image_result("not json at all")


def test_parse_image2image_result_raises_on_failure_status():
    bad = json.dumps(
        {
            "submit_id": "x",
            "gen_status": "failed",
            "result_json": {"images": [], "videos": []},
        }
    )
    with pytest.raises(OutputNotFound):
        parse_image2image_result(bad)


def test_parse_image2image_result_raises_on_empty_images():
    bad = json.dumps(
        {
            "submit_id": "x",
            "gen_status": "success",
            "result_json": {"images": [], "videos": []},
        }
    )
    with pytest.raises(OutputNotFound):
        parse_image2image_result(bad)


def test_parse_image2image_result_raises_when_image_url_missing():
    bad = json.dumps(
        {
            "submit_id": "x",
            "gen_status": "success",
            "result_json": {"images": [{"width": 100, "height": 100}], "videos": []},
        }
    )
    with pytest.raises(OutputNotFound):
        parse_image2image_result(bad)


@pytest.mark.parametrize("pending_status", ["querying", "pending", "queueing", "processing"])
def test_parse_image2image_result_raises_pending_for_in_progress(pending_status):
    pending = json.dumps(
        {
            "submit_id": "abc123",
            "gen_status": pending_status,
            "queue_info": {"queue_status": "Generating"},
        }
    )
    with pytest.raises(Image2ImageStillPending) as exc_info:
        parse_image2image_result(pending)
    assert exc_info.value.submit_id == "abc123"
    assert exc_info.value.gen_status == pending_status


# ---------- run_query_result + wait_for_result ----------

def test_run_query_result_returns_stdout(monkeypatch):
    fake = MagicMock(returncode=0, stdout=SAMPLE_SUCCESS_STDOUT, stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
    assert run_query_result("abc") == SAMPLE_SUCCESS_STDOUT


def test_run_query_result_raises_on_failure(monkeypatch):
    fake = MagicMock(returncode=1, stdout="", stderr="not found")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake)
    with pytest.raises(DreaminaCallFailed):
        run_query_result("abc")


def test_wait_for_result_returns_when_status_becomes_success(monkeypatch):
    """wait_for_result should poll until success and return the parsed result."""
    pending_response = json.dumps(
        {"submit_id": "x", "gen_status": "querying", "queue_info": {}}
    )
    call_count = {"n": 0}

    def fake_run_query_result(submit_id):
        call_count["n"] += 1
        # First two calls return pending, third returns success
        if call_count["n"] < 3:
            return pending_response
        return SAMPLE_SUCCESS_STDOUT

    monkeypatch.setattr(dreamina_mod, "run_query_result", fake_run_query_result)
    monkeypatch.setattr("time.sleep", lambda s: None)  # don't actually sleep

    result = wait_for_result("x", max_wait_seconds=60, poll_interval_seconds=0)
    assert result.image_url.startswith("https://")
    assert call_count["n"] == 3


def test_wait_for_result_raises_on_timeout(monkeypatch):
    """wait_for_result raises OutputNotFound after max_wait_seconds."""
    pending_response = json.dumps(
        {"submit_id": "x", "gen_status": "querying", "queue_info": {}}
    )
    monkeypatch.setattr(
        dreamina_mod, "run_query_result", lambda sid: pending_response
    )
    monkeypatch.setattr("time.sleep", lambda s: None)
    # Use 0-second budget so the loop times out after exactly one query
    with pytest.raises(OutputNotFound):
        wait_for_result("x", max_wait_seconds=0, poll_interval_seconds=0)


# ---------- download_image ----------

class _FakeResponse:
    def __init__(self, status: int, payload: bytes):
        self.status = status
        self._payload = payload
    def read(self):
        return self._payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


def test_download_image_writes_remote_bytes_to_dest(monkeypatch, tmp_path: Path):
    payload = b"\x89PNG\r\n\x1a\nfake png bytes"
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=120: _FakeResponse(200, payload),
    )

    dest = tmp_path / "subdir" / "out.png"
    result = download_image("https://example.com/x.png", dest)

    assert result == dest
    assert dest.exists()
    assert dest.read_bytes() == payload


def test_download_image_creates_parent_dirs(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=120: _FakeResponse(200, b"x"),
    )
    dest = tmp_path / "a" / "b" / "c" / "out.png"
    download_image("https://example.com/x.png", dest)
    assert dest.exists()


def test_download_image_raises_on_url_error(monkeypatch, tmp_path: Path):
    def boom(req, timeout=120):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr("urllib.request.urlopen", boom)

    with pytest.raises(OutputNotFound):
        download_image("https://example.com/x.png", tmp_path / "out.png")


def test_download_image_raises_on_http_error(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=120: _FakeResponse(404, b""),
    )
    with pytest.raises(OutputNotFound):
        download_image("https://example.com/x.png", tmp_path / "out.png")
