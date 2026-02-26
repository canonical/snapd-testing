#!/usr/bin/env python3
"""
Bulk update Launchpad bug tasks based on last activity date.

This script:
- Accepts a Launchpad URL (project or source package)
- Filters bugs older than a cutoff date
- Skips specified priorities
- Skips specified statuses
- Optionally filters by current status (--from-status)
- Adds a predefined comment
- Updates bug task status (--to-status)

IMPORTANT:
    Dry-run is ENABLED by default.
    Use --apply to actually modify bugs.

Supported URLs:
    https://launchpad.net/<project>
    https://launchpad.net/<distribution>/+source/<package>

Dependencies:
    sudo apt install launchpadlib python3-keyring

Example usage:

    # Absolute cutoff date (ISO format)
    ./lp-bulk-update.py https://launchpad.net/snapd 2018-01-01

    # Absolute cutoff date (compact format)
    ./lp-bulk-update.py https://launchpad.net/snapd 20180101

    # Relative cutoff
    ./lp-bulk-update.py https://launchpad.net/snapd 2y
    ./lp-bulk-update.py https://launchpad.net/snapd 2y6m
    ./lp-bulk-update.py https://launchpad.net/snapd 18m
    ./lp-bulk-update.py https://launchpad.net/snapd 90d

    # Source package example (dry-run by default)
    ./lp-bulk-update.py \
        https://launchpad.net/ubuntu/+source/snapd \
        2y6m \
        --from-status New,Confirmed

    # Default behavior:
    #   - skips statuses: Incomplete, Fix Committed, Fix Released, Expired, Invalid
    #   - skips priority: Critical
    ./lp-bulk-update.py https://launchpad.net/snapd 2y

    # Do NOT skip any statuses
    ./lp-bulk-update.py https://launchpad.net/snapd 2y \
        --skip-statuses ""

    # Do NOT skip any priorities
    ./lp-bulk-update.py https://launchpad.net/snapd 2y \
        --skip-priorities ""

    # Custom skipped statuses and priorities
    ./lp-bulk-update.py https://launchpad.net/snapd 18m \
        --skip-statuses Incomplete,Invalid \
        --skip-priorities High,Critical

    # Apply changes (explicit)
    ./lp-bulk-update.py \
        https://launchpad.net/ubuntu/+source/snapd \
        2y6m \
        --from-status New,Triaged \
        --to-status Incomplete \
        --apply
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from launchpadlib.launchpad import Launchpad


DEFAULT_MESSAGE = (
    "Thank you for raising this issue and we'd like to apologize for not "
    "responding before now.\n\n"
    "In an attempt to stop such delays, we're resetting our backlog. "
    "This will enable us to better focus on issues we know are critical "
    "and active, but it means we need to mark older issues like these "
    "as incomplete.\n\n"
    "If this issue is still relevant and reproducible, we'd like to "
    "strongly encourage you to re-open the issue. We will then be able "
    "to prioritize a review and respond more promptly.\n\n"
    "Thank you for your patience and for supporting our project.\n\n"
    "The SnapD and Ubuntu Core team"
)

# Launchpad enums (stable, global)
VALID_STATUSES = {
    "New",
    "Incomplete",
    "Opinion",
    "Invalid",
    "Won't Fix",
    "Expired",
    "Confirmed",
    "Triaged",
    "In Progress",
    "Fix Committed",
    "Fix Released",
}

VALID_PRIORITIES = {
    "Critical",
    "High",
    "Medium",
    "Low",
    "Wishlist",
}

DEFAULT_SKIP_STATUSES = {
    "Incomplete",
    "Fix Committed",
    "Fix Released",
    "Expired",
    "Invalid",
}

DURATION_RE = re.compile(
    r"^(?:(?P<years>\d+)y)?(?:(?P<months>\d+)m)?(?:(?P<days>\d+)d)?$"
)


def parse_cutoff(value):
    """Parse cutoff date or duration."""
    now = datetime.now(timezone.utc)

    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    match = DURATION_RE.match(value)
    if match:
        years = int(match.group("years") or 0)
        months = int(match.group("months") or 0)
        days = int(match.group("days") or 0)

        delta_days = years * 365 + months * 30 + days
        if delta_days <= 0:
            print("Duration must be greater than zero")
            sys.exit(1)

        return now - timedelta(days=delta_days)

    print(
        "Invalid cutoff format.\n"
        "Use YYYY-MM-DD, YYYYMMDD, or duration like 2y6m, 18m, 90d"
    )
    sys.exit(1)


def login(consumer_name, credentials_dir):
    """Authenticate to Launchpad."""
    if credentials_dir:
        credentials_dir = os.path.expanduser(credentials_dir)

    print("Connecting to Launchpad...")
    return Launchpad.login_with(
        consumer_name,
        "production",
        version="devel",
        credentials_file=credentials_dir,
    )


def parse_launchpad_url(launchpad, url, status_filter=None):
    """Return bug tasks from a Launchpad project or source package."""
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")

    if len(parts) == 1:
        print(f"Detected project: {parts[0]}")
        project = launchpad.projects[parts[0]]
        return (
            project.searchTasks(status=status_filter)
            if status_filter
            else project.searchTasks()
        )

    if len(parts) == 3 and parts[1] == "+source":
        distro, source = parts[0], parts[2]
        print(f"Detected source package: {distro}/+source/{source}")
        sp = launchpad.distributions[distro].getSourcePackage(name=source)
        return (
            sp.searchTasks(status=status_filter)
            if status_filter
            else sp.searchTasks()
        )

    print("Unsupported Launchpad URL format.")
    sys.exit(1)


def validate_values(values, valid_set, what):
    """Validate CLI values."""
    invalid = sorted(set(values) - valid_set)
    if invalid:
        print(
            f"Invalid {what}: {', '.join(invalid)}\n"
            f"Valid values are: {', '.join(sorted(valid_set))}"
        )
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Bulk update Launchpad bugs older than cutoff date"
    )

    parser.add_argument("url", help="Launchpad URL")
    parser.add_argument(
        "cutoff_date",
        help="YYYY-MM-DD, YYYYMMDD, or duration like 2y6m",
    )
    parser.add_argument("--from-status", help="Comma-separated statuses")
    parser.add_argument(
        "--skip-statuses",
        default=",".join(sorted(DEFAULT_SKIP_STATUSES)),
        help=(
            "Comma-separated statuses to skip "
            f'(default: "{",".join(sorted(DEFAULT_SKIP_STATUSES))}"; '
            'use "" to skip none)'
        ),
    )
    parser.add_argument(
        "--to-status",
        default="Incomplete",
        help="Target status (default: Incomplete)",
    )
    parser.add_argument(
        "--skip-priorities",
        default="Critical",
        help='Comma-separated priorities to skip (use "" to skip none)',
    )
    parser.add_argument("--message", help="Comment message")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry-run)",
    )
    parser.add_argument("--credentials-dir")
    parser.add_argument(
        "--consumer-name",
        default="bulk-bug-update-script",
    )

    args = parser.parse_args()

    dry_run = not args.apply
    cutoff_date = parse_cutoff(args.cutoff_date)

    skip_statuses = [
        s.strip().title()
        for s in args.skip_statuses.split(",")
        if s.strip()
    ]

    skip_priorities = [
        p.strip().title()
        for p in args.skip_priorities.split(",")
        if p.strip()
    ]

    allowed_from_statuses = (
        [s.strip().title() for s in args.from_status.split(",") if s.strip()]
        if args.from_status
        else None
    )

    to_status = args.to_status.title()

    validate_values(skip_priorities, VALID_PRIORITIES, "priorities")
    validate_values(skip_statuses, VALID_STATUSES, "statuses (--skip-statuses)")

    if allowed_from_statuses:
        validate_values(allowed_from_statuses, VALID_STATUSES, "statuses (--from-status)")

    validate_values([to_status], VALID_STATUSES, "status (--to-status)")

    launchpad = login(args.consumer_name, args.credentials_dir)

    tasks = parse_launchpad_url(
        launchpad,
        args.url,
        status_filter=allowed_from_statuses,
    )

    message = args.message or DEFAULT_MESSAGE

    print("-" * 60)
    print(f"Cutoff date: {cutoff_date}")
    print(f"From status: {allowed_from_statuses}")
    print(f"To status: {to_status}")
    print(f"Skip statuses: {skip_statuses}")
    print(f"Skip priorities: {skip_priorities}")
    print(f"Dry run: {dry_run}")
    print("-" * 60)

    updated = examined = 0

    print(f"Checking {len(tasks)} bugs...")
    for task in tasks:
        examined += 1
        bug = task.bug
        last_activity = bug.date_last_updated

        if not last_activity:
            continue

        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)

        if task.importance in skip_priorities:
            continue

        if task.status in skip_statuses:
            continue

        if allowed_from_statuses and task.status not in allowed_from_statuses:
            continue

        if last_activity < cutoff_date:
            print(
                f"Bug #{bug.id} | "
                f"Priority: {task.importance} | "
                f"Last activity: {last_activity} | "
                f"{task.status} → {to_status}"
            )

            if not dry_run:
                bug.newMessage(content=message)
                task.status = to_status
                task.lp_save()

            updated += 1

    print("-" * 60)
    print(f"Examined: {examined}")
    print(
        f"{updated} bugs "
        f"{'would be updated' if dry_run else 'updated'}."
    )


if __name__ == "__main__":
    main()
