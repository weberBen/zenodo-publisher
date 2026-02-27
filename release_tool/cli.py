"""CLI entry point with auto-generated argparse from config schema."""

import argparse
import hashlib
import os
import sys
from pathlib import Path

from .config import Config, find_project_root, load_env, NotInitializedError
from .config_schema import OPTIONS


# ---------------------------------------------------------------------------
# Parser helpers
# ---------------------------------------------------------------------------

def _add_release_flags(parser: argparse.ArgumentParser) -> None:
    """Add --work-dir and all schema-driven flags to *parser*."""
    parser.add_argument(
        "--work-dir", type=str, default=None,
        help="Working directory (default: current directory)",
    )
    # parser.add_argument(
    #     "--debug", type=bool, default=False,
    #     help="Debug mode",
    # )

    for opt in OPTIONS:
        if not opt.cli:
            continue

        flag = f"--{opt.name.replace('_', '-')}"

        help_text = opt.help
        if opt.default not in (None, "", [], True, False):
            help_text += f" (default: {opt.default})"

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
                help=help_text,
            )


def build_parser() -> argparse.ArgumentParser:
    """Build argparse parser with subcommands and backward-compatible top-level flags."""
    parser = argparse.ArgumentParser(
        description="Release tool for Zenodo project"
    )

    # Top-level: all release flags for backward compat (bare `zp --flag`)
    _add_release_flags(parser)

    subparsers = parser.add_subparsers(dest="command")

    # --- zp release --------------------------------------------------------
    release_p = subparsers.add_parser(
        "release", help="Run the full release pipeline (default when no subcommand)")
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
    archive_p.add_argument(
        "--tag", required=True,
        help="Git tag to archive")
    archive_p.add_argument(
        "--project-name", default=None,
        help="Project name for archive prefix (default: from .zenodo.env or git root dir name). "
             "Required when using --remote outside a git repository")
    archive_p.add_argument(
        "--output-dir", default=None,
        help="Output directory (default: temporary directory)")
    archive_p.add_argument(
        "--remote", default=None, metavar="URL",
        help="Git remote URL – perform a shallow clone instead of using the local repo")
    archive_p.add_argument(
        "--no-cache", action="store_true", default=False,
        help="Fetch the tag from the remote origin instead of using the local repo "
             "(useful when the tag has not been fetched locally)")
    archive_p.set_defaults(func=cmd_archive)

    return parser

def setup_work_dir(args):
    if getattr(args, "work_dir", None):
        os.chdir(args.work_dir)

def setup_env(args, cli_override=True):
    project_root = None
    env_vars = None
    errors = []
    
    try:
        project_root = find_project_root()
    except Exception as e:
        if args.debug:
            raise
        errors.append(str(e))
        return (None, None), errors
    
    try:
        env_vars = load_env(project_root)
    except (RuntimeError, NotInitializedError) as e:
        if args.debug:
            raise
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
        if args.debug:
            raise
        errors.append(f"Invalid env file loading")
        return (project_root, None), errors
        
    try:
        config = Config(project_root, env_vars, cli_overrides)
    except Exception as e:
        if args.debug:
            raise
        errors.append(str(e))
        return (project_root, None), errors
    
    return (project_root, config), errors
# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_release(args):
    """Run the full release pipeline (current behavior)."""
    
    (project_root, config), errors = setup_env(args, cli_override=True)
    if len(errors)>0:
        print(f"\n❌\u274c {'\n'.join(errors)}", file=sys.stderr)
        return 
    
    from .pipeline import run_release
    run_release(config)


def cmd_archive(args):
    """Create a git archive at a given tag and print checksums."""
    from .git_operations import (
        archive_project, archive_remote_project, get_remote_url, GitError,
    )
    from .archive_operation import compute_file_hash

    (project_root, config), errors = setup_env(args, cli_override=True)
    print((project_root, config))

    tag_name = args.tag
    output_dir = Path(args.output_dir) if args.output_dir else None
    remote_url = args.remote
    no_cache = args.no_cache
    
    if remote_url:
        project_name = args.project_name
        hash_algos = []
    else:
        if not config:
            print(f"\n❌\u274c {'\n'.join(errors)}", file=sys.stderr)
            return

        project_name = config.project_name
        hash_algos = config.zenodo_identifier_hash_algorithms

    if not project_name:
        print(
            "\n❌\u274c --project-name is required when using --remote outside a ZP repository",
            file=sys.stderr,
        )
        return

    # --- Create the archive ------------------------------------------------
    file_path = None

    if remote_url:
        # Explicit remote URL
        try:
            file_path = archive_remote_project(
                remote_url, tag_name, args.project_name, output_dir=output_dir)
        except GitError as e:
            print(f"\n❌\u274c {e}", file=sys.stderr)
            return

    elif no_cache:
        # Local repo but fetch from origin
        try:
            origin_url = get_remote_url(project_root)
        except GitError as e:
            print(f"\n\u274c Could not get remote origin URL: {e}", file=sys.stderr)
            return
        try:
            file_path = archive_remote_project(
                origin_url, tag_name, project_name, output_dir=output_dir)
        except GitError as e:
            print(f"\n❌\u274c {e}", file=sys.stderr)
            return

    else:
        # Local archive
        try:
            file_path, _, _ = archive_project(
                project_root, tag_name, project_name,
                archive_dir=output_dir,
                persist=output_dir is not None,
            )
        except GitError as e:
            print(f"\n❌\u274c {e}", file=sys.stderr)
            print(
                "Hint: use --no-cache to archive from the remote origin "
                "without touching the local repo",
                file=sys.stderr,
            )
            return

    # --- Checksums ---------------------------------------------------------
    base = ['md5', 'sha256']
    hash_algos = base + [a for a in hash_algos if a not in base]
    
    # Align all labels to the longest one + ":"
    labels = ["Archive"] + hash_algos
    pad = max(len(l) for l in labels)

    print(f"{'Archive':<{pad}}:  {file_path}")
    for hash_algo in hash_algos:
        h = compute_file_hash(file_path, hash_algo)
        print(f"{hash_algo:<{pad}}:  {h}")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """CLI entry point: dispatch to subcommand or default to release."""
    parser = build_parser()
    args = parser.parse_args()

    # Handle --work-dir once, before dispatching to any subcommand.
    # This ensures it works regardless of position (before or after subcommand).
    setup_work_dir(args)

    if hasattr(args, "func"):
        args.func(args)
    else:
        cmd_release(args)
