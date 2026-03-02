import json
import os
import shutil
import hashlib
import tempfile
import jcs
from pathlib import Path

from .git_operations import archive_zip_project, extract_zip, compute_tree_hash, pack_tar
from .config_transform_common import TREE_ALGORITHMS
from .config_transform_release import COMMIT_FIELD_MAP
from . import output


def archive_preview_file(config, tag_name: str, persist: bool = True) -> Path:
    """
    Copy main.pdf to {base_name}-{tag_name}.{extension}.

    Args:
        config: Configuration object
        tag_name: Tag name (version)
        persist: If True, save to archive_dir; if False, create temp file

    Returns:
        Path to the preview file

    Raises:
        FileNotFoundError: If file doesn't exist
    """
    compile_dir = Path(config.compile_dir)
    main_file = compile_dir / f"{config.main_file}.{config.main_file_extension}"

    if not main_file.exists():
        raise FileNotFoundError(
            f"main file not found at {main_file}\n"
            f"Make sure compilation completed successfully"
        )

    filename = f"{config.project_name}-{tag_name}"
    extension = config.main_file_extension
    new_name = f"{filename}.{extension}"

    if persist and config.archive_dir:
        new_file = config.archive_dir / new_name
    else:
        new_file = Path(tempfile.gettempdir()) / new_name

    output.info(f"📝 Copying preview file: {main_file.name} → {new_file}")
    shutil.copy(main_file, new_file)
    output.info_ok(f"File copied to {new_file}")

    return new_file, filename, extension

def format_hash_info(algorithm, hex_value):
    return {
        "type": algorithm,
        "value": hex_value,
        "formatted_value": f"{algorithm}:{hex_value}"
    }

def compute_file_hash(file_path: Path, algorithm: str) -> dict:
    """Compute hash of a file. Returns {"value": hex, "formatted_value": "algo:hex"}."""
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    hex_value = h.hexdigest()
    
    return format_hash_info(algorithm, hex_value)


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

    with tempfile.TemporaryDirectory() as tmp:
        content_dir = extract_zip(zip_path, Path(tmp))

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

    return zip_path, "zip", tree_hashes


def compute_hashes(results, algorithms=None):
    """Compute all required hashes for each archived entry.

    Always computes md5 and sha256. Adds any extra algorithms from config.
    For tree algorithms on project entries, expects pre-computed values in entry dict.
    For tree algorithms on non-project entries, falls back to the corresponding hashlib algo.

    Stores results as entry["hashes"] dict of {algo: {value, formatted_value}}.
    """
    all_algos = {"md5", "sha256"} | set(algorithms or [])
    tree_algos = {a for a in all_algos if a in TREE_ALGORITHMS}
    file_algos = all_algos - tree_algos

    for entry in results:
        hashes = {}

        for algo in file_algos:
            hashes[algo] = compute_file_hash(entry["file_path"], algo)

        for algo in tree_algos:
            if entry["type"] == "project":
                # tree hashes must be pre-computed by caller (single extraction)
                if algo not in entry:
                    raise ValueError(f"Tree hash '{algo}' not pre-computed for project entry")
                value = entry.pop(algo)
                hashes[algo] = format_hash_info(algo, value)
            else:
                # hashlib algo (e.g. sha1) but label with tree algo name
                raw = compute_file_hash(entry["file_path"], TREE_ALGORITHMS[algo])
                hashes[algo] = format_hash_info(algo, raw['value'])

        entry["hashes"] = hashes


def archive(config, tag_name: str) -> list:
    """
    Create archives and compute checksums.

    Uses config.archive_types to determine what to archive (pdf, project).
    Uses config.persist_types to determine what to persist to archive_dir.

    Returns:
        List of archived file entries
    """
    results = []

    if config.main_file_extension in config.archive_types:
        persist_file = config.main_file_extension in config.persist_types
        file_path, filename, extension = archive_preview_file(config, tag_name, persist=persist_file)
        is_preview = (config.main_file_extension == extension)
        results.append({
            "file_path": file_path,
            "is_preview": is_preview,
            "filename": filename,
            "extension": extension,
            "type": "main_file",
            "persist": persist_file,
            "is_signature": False,
        })

    if "project" in config.archive_types:
        persist_file = "project" in config.persist_types
        result = archive_zip_project(
            config.project_root,
            tag_name,
            config.project_name,
            archive_dir=config.archive_dir,
            persist=persist_file,
        )
        file_path = result.file_path
        is_preview = (config.main_file_extension == result.format)
        entry = {
            "file_path": file_path,
            "is_preview": is_preview,
            "filename": result.archive_name,
            "extension": result.format,
            "type": "project",
            "persist": persist_file,
            "is_signature": False,
        }

        results.append(entry)

    _postprocess(config, results)

    return results


def _postprocess(config, results):
    """Process project archive (tree hashes, TAR) and compute all hashes."""
    hash_algos = list(config.hash_algorithms or [])
    tree_algos = [a for a in hash_algos if a in TREE_ALGORITHMS]
    project_entry = next((e for e in results if e["type"] == "project"), None)

    if project_entry:
        final_path, final_format, tree_hashes = process_project_archive(
            project_entry["file_path"], project_entry["filename"],
            tree_algos=tree_algos, archive_format=config.archive_format,
            tar_args=config.archive_tar_extra_args,
            gzip_args=config.archive_gzip_extra_args,
        )
        project_entry["file_path"] = final_path
        project_entry["extension"] = final_format
        project_entry.update(tree_hashes)

    compute_hashes(results, hash_algos)


def generate_manifest(archived_files, version, commit_info,
                      commit_fields=None, metadata=None) -> dict:
    """Generate a manifest dict listing all archives with their hashes.

    Args:
        archived_files: List of entry dicts (with hashes computed).
        version: Tag name / version string.
        commit_info: Dict with ZP_* keys from the pipeline.
        commit_fields: List of field names to include (keys of COMMIT_FIELD_MAP).
                       Defaults to ["sha", "date_epoch"].
        metadata: Optional dict of metadata fields to include.

    Returns:
        Manifest dict.
    """
    
    commit = {}
    has_tag_sha = False
    
    for field in commit_fields:
        zp_key = COMMIT_FIELD_MAP.get(field)
        if zp_key == "ZP_TAG_SHA":
            has_tag_sha = True
            continue
        if zp_key and zp_key in commit_info:
            commit[field] = commit_info[zp_key]

    version_info = {"label": version}
    if has_tag_sha:
        version_info["sha"] = commit_info["ZP_TAG_SHA"]

    manifest = {
        "version": version_info,
        "commit": commit,
        "files": [
            {
                "key": e["file_path"].name,
                **{algo: h["value"] for algo, h in e["hashes"].items()},
            }
            for e in archived_files
            if not e.get("is_signature")
        ],
    }

    if metadata:
        manifest["metadata"] = metadata

    return manifest

def manifest_to_file(manifest: dict, output_dir: Path = None) -> Path:
    """Write manifest dict to a canonical JSON file (JCS / RFC 8785).

    Args:
        manifest: Manifest dict to serialize.
        output_dir: Directory for the file (defaults to system temp dir).

    Returns:
        Path to the written file.
    """
    if output_dir is None:
        output_dir = Path(tempfile.gettempdir())

    output_file = output_dir / "manifest.json"
    canonical = jcs.canonicalize(manifest)

    with open(output_file, "wb") as f:
        f.write(canonical)

    return output_file