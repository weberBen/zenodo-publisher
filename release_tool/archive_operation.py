"""Archive operations: ArchivedFile dataclass, hashing, manifest generation."""

import json
import os
import shutil
import hashlib
import jcs
from dataclasses import dataclass, field
from pathlib import Path

from .git_operations import archive_zip_project, extract_zip, compute_tree_hash, pack_tar
from .config.transform_common import TREE_ALGORITHMS
from .config.transform_release import COMMIT_FIELD_MAP
from .config.generated_files import PublisherDestinations
from . import output


# ---------------------------------------------------------------------------
# ArchivedFile dataclass — replaces the old dict-based entries
# ---------------------------------------------------------------------------

@dataclass
class ArchivedFile:
    """Runtime representation of a file in the pipeline."""
    file_path: Path
    config_key: str               # references FileEntry.key (e.g. "paper", "project")
    filename: str
    extension: str
    kind: str                     # "generated", "project", "manifest", "signature"
    is_preview: bool = False
    is_signature: bool = False
    has_signature: bool = False
    persist: bool = True
    hashes: dict = field(default_factory=dict)
    publishers: PublisherDestinations | None = None
    signed_file_key: str | None = None
    identifier_value: str | None = None


# ---------------------------------------------------------------------------
# Hash utilities
# ---------------------------------------------------------------------------

def format_hash_info(algorithm, hex_value):
    return {
        "type": algorithm,
        "value": hex_value,
        "formatted_value": f"{algorithm}:{hex_value}"
    }


def compute_file_hash(file_path: Path, algorithm: str) -> dict:
    """Compute hash of a file. Returns {"type", "value", "formatted_value"}."""
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    hex_value = h.hexdigest()
    return format_hash_info(algorithm, hex_value)


# ---------------------------------------------------------------------------
# Archive processing
# ---------------------------------------------------------------------------

def process_project_archive(zip_path, filename, tree_algos=None, archive_format="zip",
                            tar_args=None, gzip_args=None):
    """Extract zip once for tree hashes and/or TAR conversion.

    Returns (final_path, final_format, tree_hashes) where tree_hashes is {algo: hash}.
    """
    tree_algos = tree_algos or []
    need_tar = archive_format in ("tar", "tar.gz")
    tree_hashes = {}

    if not tree_algos and not need_tar:
        return zip_path, "zip", tree_hashes

    extract_dir = zip_path.parent / "_content"
    extract_dir.mkdir()
    try:
        content_dir = extract_zip(zip_path, extract_dir)

        for algo in tree_algos:
            tree_hashes[algo] = compute_tree_hash(content_dir, TREE_ALGORITHMS[algo])

        if need_tar:
            compress_gz = archive_format == "tar.gz"
            ext = "tar.gz" if compress_gz else "tar"
            tar_path = zip_path.parent / f"{filename}.{ext}"
            env = {**os.environ, "LC_ALL": "C", "TZ": "UTC", "SOURCE_DATE_EPOCH": "0"}
            pack_tar(content_dir, tar_path, compress_gz=compress_gz,
                     tar_args=tar_args, gzip_args=gzip_args, env=env)
            zip_path.unlink()
            return tar_path, archive_format, tree_hashes
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)

    return zip_path, "zip", tree_hashes


# ---------------------------------------------------------------------------
# Hashing for ArchivedFile entries
# ---------------------------------------------------------------------------

def compute_hashes(entries: list[ArchivedFile], algorithms: list[str] | None = None) -> None:
    """Compute all required hashes for each ArchivedFile entry.

    Always computes md5 and sha256. Adds any extra algorithms from config.
    Skips algorithms already present in entry.hashes (e.g. pre-computed tree hashes).
    """
    all_algos = {"md5", "sha256"} | set(algorithms or [])
    tree_algos = {a for a in all_algos if a in TREE_ALGORITHMS}
    file_algos = all_algos - tree_algos

    for entry in entries:
        hashes = dict(entry.hashes)  # keep pre-computed hashes

        for algo in file_algos:
            if algo not in hashes:
                hashes[algo] = compute_file_hash(entry.file_path, algo)

        for algo in tree_algos:
            if algo in hashes:
                # already pre-computed (e.g. tree hashes for project)
                continue
            if entry.kind == "project":
                # tree hashes must be pre-computed by caller (single extraction)
                raise ValueError(f"Tree hash '{algo}' not pre-computed for project entry")
            # hashlib algo (e.g. sha1) but label with tree algo name
            raw = compute_file_hash(entry.file_path, TREE_ALGORITHMS[algo])
            hashes[algo] = format_hash_info(algo, raw['value'])

        entry.hashes = hashes


# ---------------------------------------------------------------------------
# Manifest generation
# ---------------------------------------------------------------------------

def generate_manifest(archived_files: list[ArchivedFile], version: str,
                      commit_info: dict, commit_fields: list[str] | None = None,
                      metadata: dict | None = None) -> dict:
    """Generate a manifest dict listing archived files with their hashes.

    Args:
        archived_files: List of ArchivedFile entries (with hashes computed).
        version: Tag name / version string.
        commit_info: Dict with ZP_* keys from the pipeline.
        commit_fields: List of field names to include (keys of COMMIT_FIELD_MAP).
        metadata: Optional dict of metadata fields to include.

    Returns:
        Manifest dict.
    """
    commit_fields = commit_fields or ["sha", "date_epoch"]

    commit = {}
    has_tag_sha = False

    for f in commit_fields:
        zp_key = COMMIT_FIELD_MAP.get(f)
        if zp_key == "ZP_TAG_SHA":
            has_tag_sha = True
            continue
        if zp_key and zp_key in commit_info:
            commit[f] = commit_info[zp_key]

    version_info = {"label": version}
    if has_tag_sha:
        version_info["sha"] = commit_info.get("ZP_TAG_SHA", "")

    manifest = {
        "version": version_info,
        "commit": commit,
        "files": [
            {
                "key": entry.file_path.name,
                **{algo: h["value"] for algo, h in entry.hashes.items()},
            }
            for entry in archived_files
            if not entry.is_signature
        ],
    }

    if metadata:
        manifest["metadata"] = metadata

    return manifest


def manifest_to_file(config, manifest: dict, output_dir: Path) -> Path:
    """Write manifest dict to a canonical JSON file (JCS / RFC 8785)."""
    output_file = output_dir / f"manifest{config.project_name_template[-1]}.json"
    canonical = jcs.canonicalize(manifest)

    with open(output_file, "wb") as f:
        f.write(canonical)

    return output_file
