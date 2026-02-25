#!/usr/bin/env python3
"""
Bulk update Launchpad bug tasks based on last activity date.

This script:
- Accepts a Launchpad URL (project or source package)
- Filters bugs older than a cutoff date
- Skips specified priorities
- Optionally filters by current status (--from-status)
- Adds a predefined comment
- Updates bug task status (--to-status)

Supported URLs:
    https://launchpad.net/<project>
    https://launchpad.net/<distribution>/+source/<package>

Dependencies:
    sudo apt install launchpadlib python3-keyring
"""

import argparse
import sys
import os
from urllib.parse import urlparse
from datetime import datetime, timezone
from launchpadlib.launchpad import Launchpad


def build_default_message(cutoff_date_str):
    """Build default message including cutoff date."""
    return (
        "This bug is being updated automatically.\n\n"
        f"The bug has not had activity since before {cutoff_date_str} "
        "and is being updated accordingly."
    )


def parse_date(date_str):
    """Parse YYYY-MM-DD into UTC datetime."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        print("Date must be in YYYY-MM-DD format")
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
    path_parts = parsed.path.strip("/").split("/")

    if len(path_parts) == 1:
        project_name = path_parts[0]
        print(f"Detected project: {project_name}")
        project = launchpad.projects[project_name]
        return (
            project.searchTasks(status=status_filter)
            if status_filter else project.searchTasks()
        )

    if len(path_parts) == 3 and path_parts[1] == "+source":
        distro_name = path_parts[0]
        source_name = path_parts[2]

        print(f"Detected source package: {distro_name}/+source/{source_name}")

        distro = launchpad.distributions[distro_name]
        source_package = distro.getSourcePackage(name=source_name)

        return (
            source_package.searchTasks(status=status_filter)
            if status_filter else source_package.searchTasks()
        )

    print("Unsupported Launchpad URL format.")
    sys.exit(1)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Bulk update Launchpad bugs older than cutoff date"
    )

    parser.add_argument("url", help="Launchpad URL")
    parser.add_argument("cutoff_date", help="Cutoff date (YYYY-MM-DD)")

    parser.add_argument(
        "--from-status",
        help="Comma-separated list of current statuses required",
    )

    parser.add_argument(
        "--to-status",
        default="Incomplete",
        help="Status to set (default: Incomplete)",
    )

    parser.add_argument(
        "--skip-priorities",
        default="Critical",
        help="Comma-separated priorities to skip (default: Critical)",
    )

    parser.add_argument(
        "--message",
        help="Comment message (if omitted, auto-generated)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not apply changes",
    )

    parser.add_argument(
        "--credentials-dir",
        help="Directory containing Launchpad credentials",
    )

    parser.add_argument(
        "--consumer-name",
        default="bulk-bug-update-script",
        help="Launchpad OAuth consumer name",
    )

    args = parser.parse_args()

    cutoff_date = parse_date(args.cutoff_date)

    skip_priorities = [
        p.strip() for p in args.skip_priorities.split(",")
    ]

    allowed_from_statuses = None
    if args.from_status:
        allowed_from_statuses = [
            s.strip() for s in args.from_status.split(",")
        ]

    # Build dynamic default message
    message = (
        args.message
        if args.message
        else build_default_message(args.cutoff_date)
    )

    launchpad = login(args.consumer_name, args.credentials_dir)

    tasks = parse_launchpad_url(
        launchpad,
        args.url,
        status_filter=allowed_from_statuses,
    )

    print(f"Cutoff date: {cutoff_date}")
    print(f"From status: {allowed_from_statuses}")
    print(f"To status: {args.to_status}")
    print(f"Skip priorities: {skip_priorities}")
    print(f"Dry run: {args.dry_run}")
    print("-" * 60)

    updated = 0
    examined = 0

    for task in tasks:
        examined += 1

        bug = task.bug
        last_activity = bug.date_last_updated
        priority = task.importance
        current_status = task.status

        if last_activity is None:
            continue

        # Ensure timezone-safe comparison
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=timezone.utc)

        if priority in skip_priorities:
            continue

        if allowed_from_statuses and current_status not in allowed_from_statuses:
            continue

        # Only bugs OLDER than cutoff date
        if last_activity < cutoff_date:
            print(
                f"Bug #{bug.id} | "
                f"Priority: {priority} | "
                f"Last activity: {last_activity} | "
                f"{current_status} → {args.to_status}"
            )

            if not args.dry_run:
                bug.newMessage(content=message)
                task.status = args.to_status
                task.lp_save()

            updated += 1

    print("-" * 60)
    print(f"Examined: {examined}")
    print(
        f"{updated} bugs "
        f"{'would be updated' if args.dry_run else 'updated'}."
    )


if __name__ == "__main__":
    main()