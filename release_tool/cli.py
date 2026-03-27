"""CLI entry point with auto-generated argparse from config schema."""

import argparse
import os
import sys

from .config_env import ConfigError
from .config_release import ReleaseConfig
from .config_archive import ArchiveConfig


# ---------------------------------------------------------------------------
# Parser helpers
# ---------------------------------------------------------------------------

def _add_flag(parser, flag, opt_type, default, help_text, *,
              required=False, dest=None, choices=None):
    """Add a single flag to *parser*."""
    kwargs = {}
    if dest:
        # short name mapping to long name arg
        kwargs["dest"] = dest

    if opt_type == "store_true":
        parser.add_argument(
            flag, action="store_true", default=default, help=help_text,
            **kwargs)
    elif opt_type == "bool":
        parser.add_argument(
            flag, action=argparse.BooleanOptionalAction,
            default=None, help=help_text,
            **kwargs)
    else:
        kw = {"type": str, "default": default, "help": help_text, **kwargs}
        if required:
            kw["required"] = True
        if choices:
            kw["choices"] = choices
        parser.add_argument(flag, **kw)


def _add_options(parser: argparse.ArgumentParser, config_cls) -> None:
    """Add all ConfigOptions from a config class to the parser.

    Uses config_cls._cli_aliases for short names,
    config_cls._required to mark flags as required.
    """
    aliases = config_cls._cli_aliases
    required = config_cls._required

    for opt in config_cls._options:
        if not opt.cli:
            continue

        if opt.name in aliases:
            flag = f"--{aliases[opt.name]}"
            dest = opt.name
        else:
            flag = f"--{opt.name.replace('_', '-')}"
            dest = None

        help_text = opt.help
        if opt.default not in (None, "", [], True, False):
            help_text += f" (default: {opt.default})"

        _add_flag(parser, flag, opt.type, None, help_text,
                  required=(opt.name in required), dest=dest,
                  choices=opt.choices)


def _setup_subparser(parser: argparse.ArgumentParser, config_cls) -> None:
    """Add --work-dir + all config options to a subparser."""
    # --work-dir is not a ConfigOption: it's consumed before config construction
    # (chdir must happen before find_project_root).
    parser.add_argument(
        "--work-dir", type=str, default=None,
        help="Working directory (default: current directory)",
    )
    _add_options(parser, config_cls)


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
    _setup_subparser(release_p, ReleaseConfig)
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
    _setup_subparser(archive_p, ArchiveConfig)
    archive_p.set_defaults(func=cmd_archive)

    return parser


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def setup_work_dir(args):
    if getattr(args, "work_dir", None):
        os.chdir(args.work_dir)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_release(args):
    """Run the full release pipeline."""
    setup_work_dir(args)
    try:
        config = ReleaseConfig.from_args(args)
    except ConfigError as e:
        if args.debug:
            raise
        print(f"\n\u274c {e}", file=sys.stderr)
        return

    if not config.is_zp_project:
        env_path = (config.project_root / ".zenodo.env") if config.project_root else ".zenodo.env"
        print(
            f"\n\u274c Project not initialized for Zenodo publisher.\n"
            f"Missing: {env_path}",
            file=sys.stderr,
        )
        return

    from .pipeline import run_release
    run_release(config)


def cmd_archive(args):
    """Create a git archive at a given tag and print checksums."""
    setup_work_dir(args)
    try:
        config = ArchiveConfig.from_args(args)
    except ConfigError as e:
        if args.debug:
            raise
        print(f"\n\u274c {e}", file=sys.stderr)
        return

    from .pipeline import run_archive
    run_archive(config)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """CLI entry point: dispatch to subcommand or show help."""
    parser = build_parser()
    args = parser.parse_args()

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()
        sys.exit(1)
