"""Standalone archive pipeline — create a git archive and print checksums."""

from pathlib import Path
from typing import Optional

from ..git_operations import (
    archive_project, archive_remote_project, get_remote_url, GitError,
)
from ..archive_operation import compute_file_hash
from .. import output
from ._common import setup_pipeline


def run_archive(
    project_root: Optional[Path],
    config,
    tag_name: str,
    project_name: str,
    output_dir: Optional[Path],
    remote_url: Optional[str],
    no_cache: bool,
    hash_algos: list[str],
) -> None:
    """Run the archive pipeline with error handling."""
    try:
        _run_archive(
            project_root, config, tag_name, project_name,
            output_dir, remote_url, no_cache, hash_algos,
        )
    except KeyboardInterrupt:
        output.info("\nExited.")
    except Exception as e:
        if config and config.debug:
            raise
        output.fatal("Error during archive:")
        output.error(str(e))


def _run_archive(
    project_root: Optional[Path],
    config,
    tag_name: str,
    project_name: str,
    output_dir: Optional[Path],
    remote_url: Optional[str],
    no_cache: bool,
    hash_algos: list[str],
) -> None:
    """Main archive logic."""
    if config:
        setup_pipeline(config.project_name, config.debug, config.project_root)
    else:
        setup_pipeline(project_name)

    # --- Create the archive ------------------------------------------------
    file_path = None

    if remote_url:
        file_path = archive_remote_project(
            remote_url, tag_name, project_name, output_dir=output_dir)

    elif no_cache:
        origin_url = get_remote_url(project_root)
        file_path = archive_remote_project(
            origin_url, tag_name, project_name, output_dir=output_dir)

    else:
        try:
            file_path, _, _ = archive_project(
                project_root, tag_name, project_name,
                archive_dir=output_dir,
                persist=output_dir is not None,
            )
        except GitError:
            output.warn(
                "Hint: use --no-cache to archive from the remote origin "
                "without touching the local repo"
            )
            raise

    # --- Checksums ---------------------------------------------------------
    base = ['md5', 'sha256']
    hash_algos = base + [a for a in hash_algos if a not in base]

    labels = ["Archive"] + hash_algos
    pad = max(len(l) for l in labels)

    output.info(f"\n{'Archive':<{pad}}:  {file_path}")
    for algo in hash_algos:
        h = compute_file_hash(file_path, algo)
        output.info(f"{algo:<{pad}}:  {h}")
