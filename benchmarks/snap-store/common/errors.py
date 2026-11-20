"""Shared benchmark exceptions."""

from __future__ import annotations




class BenchmarkError(Exception):
    """Base class for benchmark setup and execution errors."""


class ProfileError(BenchmarkError):
    """Raised when a benchmark profile is invalid."""
