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
