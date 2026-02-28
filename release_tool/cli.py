"""CLI entry point with auto-generated argparse from config schema."""

import argparse
import os
import sys
from pathlib import Path

from .config import Config, find_project_root, load_env, NotInitializedError
from .config_schema import OPTIONS, COMMON_FLAG_NAMES, ARCHIVE_CLI_OPTIONS


# ---------------------------------------------------------------------------
# Parser helpers
# ---------------------------------------------------------------------------

def _add_flag(parser, flag, opt_type, default, help_text, *,
              required=False, metavar=None):
    """Add a single flag to *parser*."""
    if opt_type == "store_true":
        parser.add_argument(
            flag, action="store_true", default=default, help=help_text)
    elif opt_type == "bool":
        parser.add_argument(
            flag, action=argparse.BooleanOptionalAction,
            default=None, help=help_text,
        )
    else:
        kwargs = {"type": str, "default": default, "help": help_text}
        if required:
            kwargs["required"] = True
        if metavar:
            kwargs["metavar"] = metavar
        parser.add_argument(flag, **kwargs)


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    """Add flags shared by all subcommands (--work-dir, --debug/--no-debug)."""
    parser.add_argument(
        "--work-dir", type=str, default=None,
        help="Working directory (default: current directory)",
    )
    for opt in OPTIONS:
        if opt.name not in COMMON_FLAG_NAMES or not opt.cli:
            continue
        flag = f"--{opt.name.replace('_', '-')}"
        _add_flag(parser, flag, opt.type, None, opt.help)


def _add_release_flags(parser: argparse.ArgumentParser) -> None:
    """Add all schema-driven release flags to *parser*."""
    for opt in OPTIONS:
        if not opt.cli or opt.name in COMMON_FLAG_NAMES:
            continue

        flag = f"--{opt.name.replace('_', '-')}"

        help_text = opt.help
        if opt.default not in (None, "", [], True, False):
            help_text += f" (default: {opt.default})"

        _add_flag(parser, flag, opt.type, None, help_text)


def _add_archive_flags(parser: argparse.ArgumentParser) -> None:
    """Add schema-driven archive flags to *parser*."""
    for opt in ARCHIVE_CLI_OPTIONS:
        flag = f"--{opt.name.replace('_', '-')}"
        _add_flag(parser, flag, opt.type, opt.default, opt.help,
                  required=opt.required, metavar=opt.metavar)


def build_parser() -> argparse.ArgumentParser:
    """Build argparse parser with required subcommands."""
    parser = argparse.ArgumentParser(
        prog="zp",
        description="Release tool for Zenodo project",
    )

    subparsers = parser.add_subparsers(
        dest="command", title="commands", metavar="")

    # --- zp release --------------------------------------------------------
    release_p = subparsers.add_parser(
        "release", help="Run the full release pipeline")
    _add_common_flags(release_p)
    _add_release_flags(release_p)
    release_p.set_defaults(func=cmd_release)

    # --- zp archive --------------------------------------------------------
    archive_p = subparsers.add_parser(
        "archive",
        help="Create a git archive of the project at a given tag",
        epilog=(
            "Note: the project name is embedded in the archive prefix "
            "(ProjectName-tag/). Changing the project name changes the archive "
            "content and therefore its checksums. To compare with the archive on "
            "Zenodo, use the exact same project name as configured on Zenodo."
        ),
    )
    _add_common_flags(archive_p)
    _add_archive_flags(archive_p)
    archive_p.set_defaults(func=cmd_archive)

    return parser


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def setup_work_dir(args):
    if getattr(args, "work_dir", None):
        os.chdir(args.work_dir)


def setup_env(args, cli_override=True):
    project_root = None
    errors = []

    try:
        project_root = find_project_root()
    except Exception as e:
        errors.append(str(e))
        return (None, None), errors

    try:
        env_vars = load_env(project_root)
    except (RuntimeError, NotInitializedError) as e:
        errors.append(str(e))
        return (project_root, None), errors

    cli_overrides = {}
    if cli_override:
        for opt in OPTIONS:
            if not opt.cli:
                continue
            val = getattr(args, opt.name, None)
            if val is not None:
                cli_overrides[opt.name] = val

    if not env_vars:
        errors.append("Invalid env file loading")
        return (project_root, None), errors

    try:
        config = Config(project_root, env_vars, cli_overrides)
    except Exception as e:
        errors.append(str(e))
        return (project_root, None), errors

    return (project_root, config), errors


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_release(args):
    """Run the full release pipeline."""
    (project_root, config), errors = setup_env(args, cli_override=True)
    if errors:
        print(f"\n\u274c {chr(10).join(errors)}", file=sys.stderr)
        return

    from .pipeline import run_release
    run_release(config, debug=config.debug)


def cmd_archive(args):
    """Create a git archive at a given tag and print checksums."""
    tag_name = args.tag
    output_dir = Path(args.output_dir) if args.output_dir else None
    remote_url = args.remote
    no_cache = args.no_cache
    debug = args.debug

    # --- Resolve project context ---
    (project_root, config), errors = setup_env(args, cli_override=False)

    if remote_url:
        project_name = args.project_name
        hash_algos = []
    else:
        if not project_root:
            print(f"\n\u274c {chr(10).join(errors)}", file=sys.stderr)
            return

        project_name = args.project_name
        if not project_name and config:
            project_name = config.project_name
        if not project_name:
            project_name = project_root.name

        hash_algos = list(config.zenodo_identifier_hash_algorithms or []) if config else []

    if not project_name:
        print(
            "\n\u274c --project-name is required when using --remote outside a git repository",
            file=sys.stderr,
        )
        return

    # --- Resolve archive format: CLI --format > config > default "zip" ---
    archive_format = getattr(args, "format", None)
    if not archive_format and config:
        archive_format = getattr(config, "archive_format", "zip")
    if not archive_format:
        archive_format = "zip"

    valid_formats = {"zip", "tar", "tar.gz"}
    if archive_format not in valid_formats:
        print(
            f"\n\u274c Invalid format '{archive_format}'. "
            f"Must be one of: {', '.join(sorted(valid_formats))}",
            file=sys.stderr,
        )
        return

    # --- Resolve --hash: merge with existing hash_algos ---
    cli_hash = getattr(args, "hash", None)
    if cli_hash:
        extra = [h.strip() for h in cli_hash.split(",") if h.strip()]
        hash_algos += [a for a in extra if a not in hash_algos]

    # --- Resolve tar/gzip args: CLI > config > empty ---
    def _resolve_args(cli_attr, config_attr):
        cli_val = getattr(args, cli_attr, None)
        if cli_val:
            return [a.strip() for a in cli_val.split(",") if a.strip()]
        if config:
            return list(getattr(config, config_attr, None) or [])
        return []

    tar_args = _resolve_args("tar_extra_args", "archive_tar_extra_args")
    gzip_args = _resolve_args("gzip_extra_args", "archive_gzip_extra_args")

    from .pipeline import run_archive
    run_archive(
        project_root, config, tag_name, project_name,
        output_dir, remote_url, no_cache, hash_algos,
        archive_format=archive_format,
        tar_args=tar_args,
        gzip_args=gzip_args,
        debug=debug,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """CLI entry point: dispatch to subcommand or show help."""
    parser = build_parser()
    args = parser.parse_args()

    # Handle --work-dir once, before dispatching to any subcommand.
    setup_work_dir(args)

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)
