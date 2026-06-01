"""CLI entry point with auto-generated argparse from config schema."""

import argparse
import os
import sys

from . import output
from .config.env import ConfigError
from .config.yaml import CONFIG_FILENAME
from .config.test import TestConfig
from .config.release import ReleaseConfig
from .config.archive import ArchiveConfig


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


def _add_infra_flags(parser: argparse.ArgumentParser) -> None:
    """Add infrastructure flags shared by all subparsers."""
    parser.add_argument(
        "--work-dir", type=str, default=None,
        help="Working directory (default: current directory)",
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help="Path to config file (overrides auto-discovered)",
    )
    parser.add_argument(
        "--debug", action="store_true", default=False,
        help="Enable debug output",
    )
    parser.add_argument(
        "--test-mode", action="store_true", default=False,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--test-config", type=str, default=None,
        help=argparse.SUPPRESS,
    )


def _setup_subparser(parser: argparse.ArgumentParser, config_cls) -> None:
    """Add infra flags + all config options to a subparser."""
    _add_infra_flags(parser)
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

    # --- zp modules --------------------------------------------------------
    modules_p = subparsers.add_parser(
        "modules", help="Run pipeline modules in standalone mode")
    _add_infra_flags(modules_p)
    modules_sub = modules_p.add_subparsers(dest="modules_command")
    modules_sub.add_parser("list", help="List available modules")
    run_p = modules_sub.add_parser(
        "run", help="Run a module standalone", add_help=False)
    run_p.add_argument("module_name", nargs="?", default=None)
    run_p.add_argument("module_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    return parser


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def setup_work_dir(args):
    work_dir = getattr(args, "work_dir", None) or os.environ.get("ZP_WORK_DIR")
    if work_dir:
        os.chdir(work_dir)

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_release(args):
    """Run the full release pipeline."""

    try:
        config = ReleaseConfig.from_args(args)
        test = TestConfig.from_args(args)
    except ConfigError as e:
        if args.debug:
            raise
        output.fatal(str(e), name="config_error.loading", exc=e)
        return

    if not config.is_zp_project:
        config_path = (config.project_root / CONFIG_FILENAME) if config.project_root else CONFIG_FILENAME
        output.fatal(
            f"Project not initialized for Zenodo publisher. Missing: {config_path}",
            name="config_error.not_initialized",
        )
        return

    from .pipeline import run_release
    run_release(config, test=test)


def cmd_archive(args):
    """Create a git archive at a given tag and print checksums."""

    try:
        config = ArchiveConfig.from_args(args)
        test = TestConfig.from_args(args)
    except ConfigError as e:
        if args.debug:
            raise
        output.fatal(str(e), name="config_error.loading", exc=e)
        return

    from .pipeline import run_archive
    run_archive(config, test=test)


def run_cmd(args, fn):
    setup_work_dir(args)
    test_mode = getattr(args, "test_mode", False)
    debug = getattr(args, "debug", False)
    if debug:
        os.environ["ZP_DEBUG"] = "true"
    if test_mode:
        os.environ["ZP_TEST_MODE"] = "true"
    test_config = getattr(args, "test_config", None)
    if test_config:
        os.environ["ZP_TEST_CONFIG"] = test_config
    output.before_init_setup(test_mode=test_mode, debug=debug)
    
    try:
        fn(args)
    except Exception as e:
        if debug:
            raise
        output.fatal(str(e), name="config_error.loading", exc=e)

def cmd_modules(args):
    """Run pipeline modules in standalone mode."""
    from .config.env import find_project_root
    from .modules import run_module_standalone, list_modules, ModuleError

    try:
        project_root = find_project_root()
    except RuntimeError:
        project_root = None

    subcmd = getattr(args, "modules_command", None)

    if subcmd == "list":
        for name, (source, _) in list_modules(project_root).items():
            output.info(f"  {name:<30s} ({source})")
        return

    if subcmd == "run":
        module_name = getattr(args, "module_name", None)
        if not module_name:
            output.info("Usage: zp modules run <module_name> [args...]")
            output.detail("Use 'zp modules run <module_name> --help' for module-specific options.")
            return
        module_args = getattr(args, "module_args", [])
        try:
            rc = run_module_standalone(module_name, module_args,
                                      project_root=project_root,
                                      output_module=output)
        except ModuleError as e:
            output.fatal(str(e), name="module.error", exc=e)
        sys.exit(rc)

    # No subcmd — show modules help
    output.info("Usage: zp modules <list|run>")
    output.detail("list                          List available modules")
    output.detail("run <module_name> [args...]    Run a module standalone")


CMD = {
    "release": cmd_release,
    "archive": cmd_archive,
    "modules": cmd_modules,
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """CLI entry point: dispatch to subcommand or show help."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command not in CMD:
        parser.print_help()
        sys.exit(1)

    fn = CMD[args.command]
    run_cmd(args, fn)
