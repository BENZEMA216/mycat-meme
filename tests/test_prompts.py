"""Tests for the prompt template registry."""
import pytest

from mycat_meme.prompts import DEFAULT_STYLE, available_styles, get_prompt


def test_default_style_is_registered():
    assert DEFAULT_STYLE in available_styles()


def test_get_prompt_default_returns_chinese_instruction():
    prompt = get_prompt(DEFAULT_STYLE)
    assert len(prompt) > 10
    assert "第一张" in prompt
    assert "第二张" in prompt
    assert "猫" in prompt


def test_get_prompt_unknown_style_raises():
    with pytest.raises(KeyError):
        get_prompt("nonexistent-style")


def test_available_styles_returns_tuple_of_strings():
    styles = available_styles()
    assert isinstance(styles, tuple)
    assert all(isinstance(s, str) for s in styles)
    assert len(styles) >= 1
