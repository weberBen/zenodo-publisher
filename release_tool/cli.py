"""CLI entry point with auto-generated argparse from config schema."""

import argparse
import os
import sys

from .config import Config, find_project_root, load_env, NotInitializedError
from .config_schema import OPTIONS


def build_parser() -> argparse.ArgumentParser:
    """Build argparse parser automatically from OPTIONS schema."""
    parser = argparse.ArgumentParser(
        description="Release tool for Zenodo project"
    )

    # --work-dir is CLI-only (not a config option)
    parser.add_argument(
        "--work-dir", type=str, default=None,
        help="Working directory (default: current directory)",
    )

    for opt in OPTIONS:
        if not opt.cli:
            continue

        flag = f"--{opt.name.replace('_', '-')}"

        if opt.type == "bool":
            parser.add_argument(
                flag,
                action=argparse.BooleanOptionalAction,
                default=None,
                help=opt.help,
            )
        else:
            parser.add_argument(
                flag,
                type=str,
                default=None,
                help=opt.help,
            )

    return parser


def main():
    """CLI entry point: parse args, load config, run release."""
    parser = build_parser()
    args = parser.parse_args()

    if args.work_dir:
        os.chdir(args.work_dir)

    try:
        project_root = find_project_root()
        env_vars = load_env(project_root)
    except (RuntimeError, NotInitializedError) as e:
        print(f"\n❌ {e}", file=sys.stderr)
        return

    # Collect CLI overrides: only values explicitly provided
    cli_overrides = {}
    for opt in OPTIONS:
        if not opt.cli:
            continue
        val = getattr(args, opt.name, None)
        if val is not None:
            cli_overrides[opt.name] = val

    config = Config(project_root, env_vars, cli_overrides)

    from .pipeline import run_release
    run_release(config)
