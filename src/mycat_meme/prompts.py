"""Chinese instruction prompt templates for dreamina image2image."""

DEFAULT_STYLE = "default"

_PROMPTS: dict[str, str] = {
    "default": (
        "把第一张图里的猫替换成第二张图里的猫，"
        "保留原图的构图、动作、表情、背景、文字与所有非猫元素。"
        "新猫的花色、品种、毛发特征严格参考第二张图。"
    ),
}


def available_styles() -> tuple[str, ...]:
    """Return the tuple of registered prompt style keys."""
    return tuple(_PROMPTS.keys())


def get_prompt(style: str) -> str:
    """Return the prompt string for the given style key.

    Raises:
        KeyError: if the style is not registered.
    """
    if style not in _PROMPTS:
        raise KeyError(f"unknown prompt style: {style!r}; available: {available_styles()}")
    return _PROMPTS[style]
