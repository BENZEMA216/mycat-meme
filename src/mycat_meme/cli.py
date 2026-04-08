"""Click-based CLI entry point for mycat-meme.

Usage:
    mycat-meme replace MEME CAT -o OUT
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
    MycatMemeError,
    OutputNotFound,
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


if __name__ == "__main__":
    main()
