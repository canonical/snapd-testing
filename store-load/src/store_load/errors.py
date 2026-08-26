class StoreLoadError(Exception):
    """Base class for errors that can be reported directly to an operator."""


class ConfigurationError(StoreLoadError):
    """Raised when a manifest or configuration file is invalid."""


class SafetyError(StoreLoadError):
    """Raised when a requested run violates a safety boundary."""


class PreflightError(StoreLoadError):
    """Raised when a target is not ready for a load test."""
