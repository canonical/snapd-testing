"""Download-specific benchmark exceptions."""

from __future__ import annotations

from common.errors import BenchmarkError
from common.errors import ProfileError


class DownloadBenchmarkError(BenchmarkError):
    """Raised when download benchmark setup or execution fails."""


class DownloadProfileError(ProfileError):
    """Raised when the download profile is invalid."""
