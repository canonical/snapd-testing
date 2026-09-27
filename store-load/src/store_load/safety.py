import hmac

from store_load.errors import SafetyError
from store_load.models import LoadProfile, TargetConfig

ABSOLUTE_MAX_CLIENTS = 1_000
ABSOLUTE_MAX_DURATION_SECONDS = 4 * 60 * 60
ABSOLUTE_MAX_REQUESTS_PER_SECOND = 1_000.0


def validate_load_limits(target: TargetConfig, profile: LoadProfile) -> None:
    peak_clients = profile.peak_clients
    duration = profile.total_duration_seconds
    peak_rps = profile.estimated_peak_requests_per_second

    if peak_clients > ABSOLUTE_MAX_CLIENTS:
        raise SafetyError(
            f"profile requests {peak_clients} clients; absolute limit is "
            f"{ABSOLUTE_MAX_CLIENTS}"
        )
    if duration > ABSOLUTE_MAX_DURATION_SECONDS:
        raise SafetyError(
            f"profile duration is {duration} seconds; absolute limit is "
            f"{ABSOLUTE_MAX_DURATION_SECONDS}"
        )
    if peak_rps > ABSOLUTE_MAX_REQUESTS_PER_SECOND:
        raise SafetyError(
            f"profile estimates {peak_rps:.2f} requests/s; absolute limit is "
            f"{ABSOLUTE_MAX_REQUESTS_PER_SECOND:.2f}"
        )

    if peak_clients > target.max_clients:
        raise SafetyError(
            f"profile requests {peak_clients} clients; target {target.name!r} "
            f"allows at most {target.max_clients}"
        )
    if duration > target.max_duration_seconds:
        raise SafetyError(
            f"profile duration is {duration} seconds; target {target.name!r} "
            f"allows at most {target.max_duration_seconds}"
        )
    if peak_rps > target.max_requests_per_second:
        raise SafetyError(
            f"profile estimates {peak_rps:.2f} requests/s; target {target.name!r} "
            f"allows at most {target.max_requests_per_second:.2f}"
        )


def require_target_confirmation(
    target: TargetConfig, supplied_confirmation: str | None
) -> None:
    if not target.protected:
        return
    if supplied_confirmation is None or not hmac.compare_digest(
        target.name, supplied_confirmation
    ):
        raise SafetyError(
            f"target {target.name!r} is protected; pass --confirm-target "
            f"{target.name!r} for network activity"
        )
