"""Standalone archive pipeline — create a git archive and print checksums."""

from pathlib import Path
from typing import Optional

from ..git_operations import (
    ArchiveResult,
    archive_zip_project, archive_zip_remote_project, get_remote_url, GitError,
)
from ..archive_operation import compute_file_hash, process_project_archive
from ..config_transform_common import TREE_ALGORITHMS
from .. import output
from ._common import setup_pipeline


def run_archive(config) -> None:
    """Run the archive pipeline with error handling."""
    try:
        _run_archive(config)
    except KeyboardInterrupt:
        output.info("\nExited.")
    except Exception as e:
        if config.debug:
            raise
        output.fatal("Error during archive:")
        output.error(str(e))


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _step_archive(
    project_root: Optional[Path],
    tag_name: str,
    project_name: str,
    output_dir: Optional[Path],
    remote_url: Optional[str],
    no_cache: bool,
) -> ArchiveResult:
    """Create a ZIP archive from local repo or remote. Returns ArchiveResult."""
    if remote_url:
        return archive_zip_remote_project(
            remote_url, tag_name, project_name, output_dir=output_dir)

    if no_cache:
        origin_url = get_remote_url(project_root)
        output.info(f"Cloning from {origin_url}")
        return archive_zip_remote_project(
            origin_url, tag_name, project_name, output_dir=output_dir)

    try:
        return archive_zip_project(
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


def _step_display(
    result: ArchiveResult,
    all_algos: list[str],
    tree_hashes: dict[str, str],
) -> None:
    """Display archive path and checksums."""
    labels = ["Archive"] + all_algos
    pad = max(len(l) for l in labels)

    output.info(f"\n{'Archive':<{pad}}:  {result.file_path}")
    for algo in all_algos:
        if algo in tree_hashes:
            h = tree_hashes[algo]
        else:
            h = compute_file_hash(result.file_path, algo)["value"]
        output.info(f"{algo:<{pad}}:  {h}")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _run_archive(config) -> None:
    """Main archive pipeline."""
    setup_pipeline(config.project_name, config.debug, config.project_root)

    # Resolve hash algos: config + CLI --hash
    hash_algos = list(config.zenodo_identifier_hash_algorithms or [])
    cli_hash = getattr(config, "hash", None)
    if cli_hash:
        extra = [h.strip() for h in cli_hash.split(",") if h.strip()]
        hash_algos += [a for a in extra if a not in hash_algos]

    # Build algo lists
    base = ['md5', 'sha256']
    all_algos = base + [a for a in hash_algos if a not in base]
    tree_algos = [a for a in all_algos if a in TREE_ALGORITHMS]

    # archive → zip
    result = _step_archive(
        config.project_root, config.tag, config.project_name,
        config.output_dir, config.remote, config.no_cache)

    # extract → tree → tar (single extraction via shared function)
    final_path, final_format, tree_hashes = process_project_archive(
        result.file_path, result.archive_name,
        tree_algos=tree_algos, archive_format=config.archive_format,
        tar_args=config.archive_tar_extra_args,
        gzip_args=config.archive_gzip_extra_args,
    )
    result.file_path = final_path
    result.format = final_format

    # display
    _step_display(result, all_algos, tree_hashes)
