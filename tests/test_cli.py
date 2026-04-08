"""Tests for the click-based CLI entry point."""
from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image

from mycat_meme import cli, pipeline
from mycat_meme.errors import (
    DreaminaCallFailed,
    DreaminaNotInstalled,
    OutputNotFound,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def meme_file(tmp_path: Path) -> Path:
    p = tmp_path / "meme.png"
    Image.new("RGB", (800, 600), color=(200, 100, 50)).save(p)
    return p


@pytest.fixture
def cat_file(tmp_path: Path) -> Path:
    p = tmp_path / "cat.jpg"
    Image.new("RGB", (1024, 1024), color=(50, 100, 200)).save(p)
    return p


def test_cli_help(runner):
    result = runner.invoke(cli.main, ["--help"])
    assert result.exit_code == 0
    assert "replace" in result.output


def test_cli_replace_help(runner):
    result = runner.invoke(cli.main, ["replace", "--help"])
    assert result.exit_code == 0
    assert "MEME" in result.output.upper() or "meme" in result.output


def test_cli_replace_happy_path(monkeypatch, runner, tmp_path, meme_file, cat_file):
    output = tmp_path / "out.png"

    def fake_replace(**kwargs):
        assert kwargs["meme"] == meme_file
        assert kwargs["cat"] == cat_file
        assert kwargs["output"] == output
        Image.new("RGB", (800, 600), color=(0, 255, 0)).save(output)
        return output

    monkeypatch.setattr(cli, "pipeline_replace", fake_replace)

    result = runner.invoke(
        cli.main,
        ["replace", str(meme_file), str(cat_file), "-o", str(output)],
    )
    assert result.exit_code == 0, result.output
    assert output.exists()


def test_cli_replace_dreamina_not_installed(monkeypatch, runner, meme_file, cat_file, tmp_path):
    def boom(**kwargs):
        raise DreaminaNotInstalled("dreamina CLI not found")

    monkeypatch.setattr(cli, "pipeline_replace", boom)

    result = runner.invoke(
        cli.main,
        ["replace", str(meme_file), str(cat_file), "-o", str(tmp_path / "out.png")],
    )
    assert result.exit_code != 0
    assert "dreamina" in result.output.lower()
    assert "install" in result.output.lower() or "not found" in result.output.lower()


def test_cli_replace_dreamina_call_failed(monkeypatch, runner, meme_file, cat_file, tmp_path):
    def boom(**kwargs):
        raise DreaminaCallFailed(returncode=2, stderr="quota exceeded")

    monkeypatch.setattr(cli, "pipeline_replace", boom)

    result = runner.invoke(
        cli.main,
        ["replace", str(meme_file), str(cat_file), "-o", str(tmp_path / "out.png")],
    )
    assert result.exit_code != 0
    assert "quota exceeded" in result.output


def test_cli_replace_output_not_found(monkeypatch, runner, meme_file, cat_file, tmp_path):
    def boom(**kwargs):
        raise OutputNotFound("could not locate output")

    monkeypatch.setattr(cli, "pipeline_replace", boom)

    result = runner.invoke(
        cli.main,
        ["replace", str(meme_file), str(cat_file), "-o", str(tmp_path / "out.png")],
    )
    assert result.exit_code != 0
    assert "could not locate" in result.output.lower() or "not found" in result.output.lower()


def test_cli_replace_meme_must_exist(runner, tmp_path, cat_file):
    """click should reject a missing meme path before our code runs."""
    result = runner.invoke(
        cli.main,
        [
            "replace",
            str(tmp_path / "no-meme.png"),
            str(cat_file),
            "-o",
            str(tmp_path / "out.png"),
        ],
    )
    assert result.exit_code != 0


# ---------- replace-gif (v0.2) ----------

@pytest.fixture
def gif_file(tmp_path: Path) -> Path:
    p = tmp_path / "in.gif"
    p.write_bytes(b"GIF89a fake")
    return p


def test_cli_replace_gif_help_lists_command(runner):
    result = runner.invoke(cli.main, ["--help"])
    assert result.exit_code == 0
    assert "replace-gif" in result.output


def test_cli_replace_gif_help(runner):
    result = runner.invoke(cli.main, ["replace-gif", "--help"])
    assert result.exit_code == 0
    assert "GIF" in result.output.upper()
    assert "--duration" in result.output
    assert "--model" in result.output


def test_cli_replace_gif_happy_path(monkeypatch, runner, tmp_path, gif_file, cat_file):
    output = tmp_path / "out.gif"

    def fake_replace_gif(**kwargs):
        assert kwargs["gif"] == gif_file
        assert kwargs["cat"] == cat_file
        assert kwargs["output"] == output
        Path(output).write_bytes(b"GIF89a result")
        return output

    monkeypatch.setattr(cli, "pipeline_replace_gif", fake_replace_gif)

    result = runner.invoke(
        cli.main,
        ["replace-gif", str(gif_file), str(cat_file), "-o", str(output)],
    )
    assert result.exit_code == 0, result.output
    assert output.exists()


def test_cli_replace_gif_passes_duration_and_model(monkeypatch, runner, tmp_path, gif_file, cat_file):
    captured = {}

    def fake_replace_gif(**kwargs):
        captured.update(kwargs)
        Path(kwargs["output"]).write_bytes(b"GIF89a")
        return kwargs["output"]

    monkeypatch.setattr(cli, "pipeline_replace_gif", fake_replace_gif)

    result = runner.invoke(
        cli.main,
        [
            "replace-gif", str(gif_file), str(cat_file),
            "-o", str(tmp_path / "out.gif"),
            "--duration", "10",
            "--model", "seedance2.0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["duration"] == 10
    assert captured["model_version"] == "seedance2.0"


def test_cli_replace_gif_ffmpeg_missing(monkeypatch, runner, tmp_path, gif_file, cat_file):
    from mycat_meme.errors import FfmpegNotInstalled

    def boom(**kwargs):
        raise FfmpegNotInstalled("ffmpeg not on PATH")

    monkeypatch.setattr(cli, "pipeline_replace_gif", boom)
    result = runner.invoke(
        cli.main,
        ["replace-gif", str(gif_file), str(cat_file), "-o", str(tmp_path / "out.gif")],
    )
    assert result.exit_code == 6
    assert "ffmpeg" in result.output.lower()


def test_cli_replace_gif_dreamina_failed(monkeypatch, runner, tmp_path, gif_file, cat_file):
    def boom(**kwargs):
        raise DreaminaCallFailed(returncode=2, stderr="quota exceeded")

    monkeypatch.setattr(cli, "pipeline_replace_gif", boom)
    result = runner.invoke(
        cli.main,
        ["replace-gif", str(gif_file), str(cat_file), "-o", str(tmp_path / "out.gif")],
    )
    assert result.exit_code == 3
    assert "quota exceeded" in result.output


def test_cli_replace_gif_input_must_exist(runner, tmp_path, cat_file):
    result = runner.invoke(
        cli.main,
        [
            "replace-gif",
            str(tmp_path / "no.gif"),
            str(cat_file),
            "-o",
            str(tmp_path / "out.gif"),
        ],
    )
    assert result.exit_code != 0
