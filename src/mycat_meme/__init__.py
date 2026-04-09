"""mycat-meme — 把表情包换成我的猫."""

__version__ = "0.2.2"

from mycat_meme.gif_pipeline import replace_gif
from mycat_meme.pipeline import replace

__all__ = ["replace", "replace_gif", "__version__"]
