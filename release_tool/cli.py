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

    # --- zp jobs -----------------------------------------------------------
    jobs_p = subparsers.add_parser(
        "jobs", help="Manage async module jobs")
    _add_infra_flags(jobs_p)
    jobs_sub = jobs_p.add_subparsers(dest="jobs_command")
    jobs_sub.add_parser("list", help="List all jobs")
    jobs_run_p = jobs_sub.add_parser("run", help="Run eligible pending jobs")
    jobs_run_p.add_argument("job_id", nargs="?", default=None,
                            help="Run a specific job by ID (or prefix)")
    jobs_run_p.add_argument("--all", action="store_true", default=False,
                            dest="run_all",
                            help="Run all pending jobs (ignore retry timing)")
    jobs_info_p = jobs_sub.add_parser("info", help="Show detailed job info")
    jobs_info_p.add_argument("job_id", help="Job ID (or prefix)")
    jobs_rm_p = jobs_sub.add_parser("rm", help="Remove a job")
    jobs_rm_p.add_argument("job_id", help="Job ID (or prefix)")
    jobs_sub.add_parser("clean", help="Remove completed jobs")

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

def cmd_release(args, test=None, debug=False):
    """Run the full release pipeline."""

    try:
        config = ReleaseConfig.from_args(args)
    except ConfigError as e:
        if debug:
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


def cmd_archive(args, test=None, debug=False):
    """Create a git archive at a given tag and print checksums."""

    try:
        config = ArchiveConfig.from_args(args)
    except ConfigError as e:
        if debug:
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

    # Load test config so prompts work in test mode for all commands
    test = None
    if test_mode:
        try:
            from .config.test import TestConfig
            test = TestConfig.from_args(args)
            output.setup(test_mode=True, test_config=test)
        except ConfigError as e:
            if debug:
                raise
            output.fatal(str(e), name="config_error.loading", exc=e)
            return

    # Auto-check for pending jobs (skip if we're already running jobs)
    pending_count = 0
    if args.command != "jobs":
        from .jobs import count_pending
        pending_count = count_pending()
        if pending_count:
            _print_jobs_notice(pending_count, test_mode=test_mode)

    try:
        fn(args, test=test, debug=debug)
    except Exception as e:
        if debug:
            raise
        output.fatal(str(e), name="config_error.loading", exc=e)

    if pending_count and args.command != "jobs":
        _print_jobs_notice(pending_count, test_mode=test_mode)


YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET_STYLE = "\033[0m"


def _print_jobs_notice(count: int, test_mode: bool = False):
    if test_mode:
        output.warn(
            "{n} async job(s) pending. Run 'zp jobs run' to process them.",
            n=count, name="jobs.pending_notice",
        )
    else:
        print(
            f"\n{YELLOW}{BOLD}"
            f">>> {count} async job(s) pending. Run 'zp jobs run' to process them."
            f"{RESET_STYLE}\n"
        )

def cmd_jobs(args, test=None, debug=False):
    """Manage async module jobs."""
    from .jobs import (
        list_jobs, print_jobs_table, print_job_info,
        run_jobs, clean_jobs, get_job, remove_job,
    )

    subcmd = getattr(args, "jobs_command", None)

    if subcmd == "list":
        jobs = list_jobs()
        print_jobs_table(jobs)
        return

    if subcmd == "run":
        run_all = getattr(args, "run_all", False)
        job_id = getattr(args, "job_id", None)
        run_jobs(run_all=run_all, job_id=job_id)
        return

    if subcmd == "info":
        job = get_job(args.job_id)
        if job is None:
            output.error("Job not found: {id}", id=args.job_id, name="jobs.not_found")
            return
        print_job_info(job)
        return

    if subcmd == "rm":
        if remove_job(args.job_id):
            output.info("Job removed: {id}", id=args.job_id, name="jobs.removed")
        else:
            output.error("Job not found: {id}", id=args.job_id, name="jobs.not_found")
        return

    if subcmd == "clean":
        removed = clean_jobs()
        output.info(f"{removed} completed job(s) removed.", name="jobs.cleaned")
        return

    # No subcmd — list (default behavior)
    jobs = list_jobs()
    print_jobs_table(jobs)


def cmd_modules(args, test=None, debug=False):
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
    "jobs": cmd_jobs,
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
