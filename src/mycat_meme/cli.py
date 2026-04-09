"""Click-based CLI entry point for mycat-meme.

Usage:
    mycat-meme replace MEME CAT -o OUT
    mycat-meme replace-gif INPUT.gif CAT -o OUT.gif
    mycat-meme --version
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from mycat_meme import __version__
from mycat_meme.errors import (
    DreaminaCallFailed,
    DreaminaNotInstalled,
    FfmpegFailed,
    FfmpegNotInstalled,
    MycatMemeError,
    OutputNotFound,
)
from mycat_meme.gif_pipeline import (
    DEFAULT_VIDEO_MODEL,
    replace_gif as pipeline_replace_gif,
)
from mycat_meme.pipeline import replace as pipeline_replace
from mycat_meme.prompts import DEFAULT_STYLE, available_styles


@click.group()
@click.version_option(__version__, prog_name="mycat-meme")
def main() -> None:
    """把表情包换成我的猫 — 由即梦 CLI 驱动."""


@main.command("replace")
@click.argument(
    "meme",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.argument(
    "cat",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-o",
    "--output",
    "output",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where to write the resulting image.",
)
@click.option(
    "--style",
    type=click.Choice(list(available_styles())),
    default=DEFAULT_STYLE,
    show_default=True,
    help="Prompt style.",
)
@click.option(
    "--poll-seconds",
    type=int,
    default=180,
    show_default=True,
    help="Max seconds to wait inline for dreamina image2image. After this, the "
         "pipeline falls back to query_result polling for up to 5 more minutes.",
)
def replace_cmd(
    meme: Path, cat: Path, output: Path, style: str, poll_seconds: int
) -> None:
    """Replace the cat in MEME with the cat photo in CAT, write to -o OUT."""
    click.echo(f"replacing... (meme={meme.name}, cat={cat.name})")
    try:
        result = pipeline_replace(
            meme=meme,
            cat=cat,
            output=output,
            style=style,
            poll_seconds=poll_seconds,
        )
    except DreaminaNotInstalled as e:
        click.echo(
            f"dreamina CLI not found: {e}\n"
            f"Install it first, then run `dreamina login`."
        )
        sys.exit(2)
    except DreaminaCallFailed as e:
        click.echo(f"dreamina image2image failed:\n{e.stderr}")
        sys.exit(3)
    except OutputNotFound as e:
        click.echo(f"could not locate dreamina output: {e}")
        sys.exit(4)
    except FileNotFoundError as e:
        click.echo(f"{e}")
        sys.exit(5)
    except MycatMemeError as e:
        click.echo(f"{e}")
        sys.exit(1)

    click.echo(f"done: {result}")


@main.command("replace-gif")
@click.argument(
    "gif",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.argument(
    "cat",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "-o",
    "--output",
    "output",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where to write the resulting GIF.",
)
@click.option(
    "--description",
    "-d",
    type=str,
    default=None,
    help="Short description of your cat (breed / fur length / color / face "
         "shape). Injected into the multimodal prompt so dreamina preserves "
         "breed-level features. Strongly recommended for non-generic cats. "
         "Example: --description '金色长毛虎斑小奶猫，蓬松长毛，圆脸幼态'",
)
@click.option(
    "--model",
    "model_version",
    type=click.Choice(
        ["seedance2.0", "seedance2.0fast", "seedance2.0_vip", "seedance2.0fast_vip"]
    ),
    default=DEFAULT_VIDEO_MODEL,
    show_default=True,
    help="dreamina seedance video model.",
)
@click.option(
    "--duration",
    type=int,
    default=None,
    help="Output video length in seconds (4-15). Defaults to ceil(input duration).",
)
@click.option(
    "--fps",
    "output_fps",
    type=int,
    default=15,
    show_default=True,
    help="Output GIF frame rate.",
)
@click.option(
    "--max-width",
    "output_max_width",
    type=int,
    default=600,
    show_default=True,
    help="Output GIF max width in pixels (height auto-scaled).",
)
@click.option(
    "--poll-seconds",
    type=int,
    default=600,
    show_default=True,
    help="Max seconds to wait for dreamina multimodal2video to finish.",
)
def replace_gif_cmd(
    gif: Path,
    cat: Path,
    output: Path,
    description: str | None,
    model_version: str,
    duration: int | None,
    output_fps: int,
    output_max_width: int,
    poll_seconds: int,
) -> None:
    """Replace the cat in GIF with the cat photo in CAT, write to -o OUT.gif."""
    desc_str = f", desc='{description}'" if description else ""
    click.echo(
        f"replacing... (gif={gif.name}, cat={cat.name}, model={model_version}{desc_str})"
    )
    if not description:
        click.echo(
            "💡 Tip: add -d \"your cat description\" for much better breed fidelity\n"
            "        例: -d \"金色长毛小奶猫，蓬松长毛，圆脸幼态\"\n"
            "        更多模板见 README 的 'Cat description cheat sheet' 章节"
        )
    try:
        result = pipeline_replace_gif(
            gif=gif,
            cat=cat,
            output=output,
            description=description,
            model_version=model_version,
            duration=duration,
            output_fps=output_fps,
            output_max_width=output_max_width,
            poll_seconds=poll_seconds,
        )
    except DreaminaNotInstalled as e:
        click.echo(
            f"dreamina CLI not found: {e}\n"
            f"Install it first, then run `dreamina login`."
        )
        sys.exit(2)
    except DreaminaCallFailed as e:
        click.echo(f"dreamina failed:\n{e.stderr}")
        sys.exit(3)
    except OutputNotFound as e:
        click.echo(f"could not locate dreamina output: {e}")
        sys.exit(4)
    except FfmpegNotInstalled as e:
        click.echo(
            f"ffmpeg not found: {e}\n"
            f"Install ffmpeg (e.g. `brew install ffmpeg`) and retry."
        )
        sys.exit(6)
    except FfmpegFailed as e:
        click.echo(f"ffmpeg failed:\n{e.stderr}")
        sys.exit(7)
    except FileNotFoundError as e:
        click.echo(f"{e}")
        sys.exit(5)
    except MycatMemeError as e:
        click.echo(f"{e}")
        sys.exit(1)

    click.echo(f"done: {result}")


if __name__ == "__main__":
    main()
