"""Custom exceptions for the mycat_meme package."""


class MycatMemeError(Exception):
    """Base class for all mycat_meme errors."""


class DreaminaNotInstalled(MycatMemeError):
    """Raised when the `dreamina` CLI binary cannot be found on PATH."""


class DreaminaCallFailed(MycatMemeError):
    """Raised when `dreamina image2image` returns a non-zero exit code."""

    def __init__(self, returncode: int, stderr: str):
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"dreamina image2image failed with exit code {returncode}:\n{stderr}"
        )


class OutputNotFound(MycatMemeError):
    """Raised when dreamina succeeded but we could not locate the output image."""


class FfmpegNotInstalled(MycatMemeError):
    """Raised when the `ffmpeg` or `ffprobe` binary cannot be found on PATH."""


class FfmpegFailed(MycatMemeError):
    """Raised when ffmpeg/ffprobe returns a non-zero exit code."""

    def __init__(self, returncode: int, stderr: str):
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"ffmpeg failed with exit code {returncode}:\n{stderr}"
        )
