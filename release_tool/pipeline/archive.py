"""Standalone archive pipeline — create a git archive and print checksums."""

import os
import tempfile
from pathlib import Path
from typing import Optional

from ..git_operations import (
    ArchiveResult,
    archive_zip_project, archive_zip_remote_project, get_remote_url, GitError,
    extract_zip, compute_tree_hash, pack_tar,
)
from ..archive_operation import compute_file_hash
from ..config_schema import TREE_ALGORITHMS
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
    archive_format: str = "zip",
    tar_args: list[str] | None = None,
    gzip_args: list[str] | None = None,
    debug=False,
) -> None:
    """Run the archive pipeline with error handling."""
    try:
        _run_archive(
            project_root, config, tag_name, project_name,
            output_dir, remote_url, no_cache, hash_algos,
            archive_format=archive_format,
            tar_args=tar_args,
            gzip_args=gzip_args,
        )
    except KeyboardInterrupt:
        output.info("\nExited.")
    except Exception as e:
        if debug:
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



def _step_tree(
    content_dir: Path,
    tree_algos: dict[str, str],
) -> dict[str, str]:
    """Compute tree hashes from extracted content. Returns {algo_name: hash}."""
    return {
        algo_name: compute_tree_hash(content_dir, obj_fmt)
        for algo_name, obj_fmt in tree_algos.items()
    }


def _step_tar(
    result: ArchiveResult,
    content_dir: Path,
    archive_format: str,
    tar_args: list[str] | None = None,
    gzip_args: list[str] | None = None,
) -> ArchiveResult:
    """Convert to TAR/TAR.GZ from extracted content. Updates result in place."""
    zip_path = result.file_path
    compress_gz = archive_format == "tar.gz"
    ext = "tar.gz" if compress_gz else "tar"
    tar_path = zip_path.parent / f"{result.archive_name}.{ext}"
    
    env = {**os.environ, "LC_ALL": "C", "TZ": "UTC", "SOURCE_DATE_EPOCH": "0"}
    pack_tar(
        content_dir, tar_path,
        compress_gz=compress_gz,
        tar_args=tar_args,
        gzip_args=gzip_args,
        env=env,
    )
    zip_path.unlink()
    
    result.file_path = tar_path
    result.format = archive_format
    
    return result


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
            h = compute_file_hash(result.file_path, algo)
        output.info(f"{algo:<{pad}}:  {h}")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def _run_archive(
    project_root: Optional[Path],
    config,
    tag_name: str,
    project_name: str,
    output_dir: Optional[Path],
    remote_url: Optional[str],
    no_cache: bool,
    hash_algos: list[str],
    archive_format: str = "zip",
    tar_args: list[str] | None = None,
    gzip_args: list[str] | None = None,
) -> None:
    """Main archive pipeline."""
    if config:
        setup_pipeline(config.project_name, config.debug, config.project_root)
    else:
        setup_pipeline(project_name)

    # Build algo lists
    base = ['md5', 'sha256']
    all_algos = base + [a for a in hash_algos if a not in base]
    tree_algos = {a: TREE_ALGORITHMS[a] for a in all_algos if a in TREE_ALGORITHMS}
    need_extract = bool(tree_algos) or archive_format != "zip"

    # archive → zip
    result = _step_archive(
        project_root, tag_name, project_name, output_dir, remote_url, no_cache)

    # extract → tree → tar (single extraction)
    tree_hashes = {}
    if need_extract:
        with tempfile.TemporaryDirectory() as tmp_dir:
            content_dir = extract_zip(result.file_path, Path(tmp_dir))
            if tree_algos:
                tree_hashes = _step_tree(content_dir, tree_algos)
            if archive_format in ("tar", "tar.gz"):
                result = _step_tar(result, content_dir, archive_format,
                                   tar_args=tar_args,
                                   gzip_args=gzip_args)

    # display
    _step_display(result, all_algos, tree_hashes)
