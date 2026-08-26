import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from store_load import __version__
from store_load.errors import StoreLoadError
from store_load.loaders import load_manifest, load_profile, load_target_catalog
from store_load.preflight import run_preflight
from store_load.safety import require_target_confirmation
from store_load.workload import ExecutionPlan, compile_execution_plan


def _validate(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    validated = [f"manifest {manifest.name!r}"]
    if args.targets is not None:
        catalog = load_target_catalog(args.targets)
        validated.append(f"{len(catalog.targets)} target(s)")
    if args.profile is not None:
        profile = load_profile(args.profile)
        validated.append(f"profile {profile.name!r}")
    print("valid: " + ", ".join(validated))
    return 0


def _load_plan(args: argparse.Namespace) -> ExecutionPlan:
    return compile_execution_plan(
        catalog=load_target_catalog(args.targets),
        target_name=args.target,
        profile=load_profile(args.profile),
        manifest=load_manifest(args.manifest),
    )


def _plan(args: argparse.Namespace) -> int:
    plan = _load_plan(args)
    print(json.dumps(plan.as_dict(), indent=2, sort_keys=True))
    return 0


def _preflight(args: argparse.Namespace) -> int:
    plan = _load_plan(args)
    if not args.skip_network:
        require_target_confirmation(plan.target, args.confirm_target)
    report = run_preflight(
        plan,
        credentials_path=args.credentials,
        check_network=not args.skip_network,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 0 if report.ok else 1


def _add_plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--profile", type=Path, required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="store-load",
        description="Validate and execute controlled Snap Store load tests.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate", help="validate workload and configuration files"
    )
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--targets", type=Path)
    validate.add_argument("--profile", type=Path)
    validate.set_defaults(handler=_validate)

    plan = subparsers.add_parser(
        "plan", help="compile and display a bounded execution plan"
    )
    _add_plan_arguments(plan)
    plan.set_defaults(handler=_plan)

    preflight = subparsers.add_parser(
        "preflight", help="check configuration and bounded target connectivity"
    )
    _add_plan_arguments(preflight)
    preflight.add_argument("--credentials", type=Path)
    preflight.add_argument("--confirm-target")
    preflight.add_argument("--skip-network", action="store_true")
    preflight.add_argument("--timeout", type=float, default=5.0)
    preflight.set_defaults(handler=_preflight)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except StoreLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2