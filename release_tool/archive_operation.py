import json
import os
import shutil
import hashlib
import jcs
from pathlib import Path

from .git_operations import archive_zip_project, extract_zip, compute_tree_hash, pack_tar
from .config_transform_common import TREE_ALGORITHMS
from .config_transform_release import COMMIT_FIELD_MAP
from . import output


def archive_preview_file(config, tag_name: str, output_dir: Path) -> Path:
    """
    Copy main.pdf to {base_name}-{tag_name}.{extension}.

    Args:
        config: Configuration object
        tag_name: Tag name (version)
        output_dir: Directory to write the copy to

    Returns:
        Tuple of (file_path, filename, extension)

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
    new_file = output_dir / new_name

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

    Extracts into the zip's parent directory (reusing the same tmp dir)
    to avoid creating a second temporary directory.

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


def archive(config, tag_name: str, output_dir: Path) -> list:
    """
    Create archives and compute checksums.

    Args:
        config: Configuration object
        tag_name: Tag name (version)
        output_dir: Directory for all output files

    Returns:
        List of archived file entries
    """
    results = []

    if config.main_file_extension in config.archive_types:
        file_path, filename, extension = archive_preview_file(config, tag_name, output_dir)
        results.append({
            "file_path": file_path,
            "is_preview": (config.main_file_extension == extension),
            "filename": filename,
            "extension": extension,
            "type": "main_file",
            "persist": config.main_file_extension in config.persist_types,
            "is_signature": False,
        })

    if "project" in config.archive_types:
        result = archive_zip_project(
            config.project_root,
            tag_name,
            config.project_name,
            output_dir,
        )
        results.append({
            "file_path": result.file_path,
            "is_preview": (config.main_file_extension == result.format),
            "filename": result.archive_name,
            "extension": result.format,
            "type": "project",
            "persist": "project" in config.persist_types,
            "is_signature": False,
        })

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

def manifest_to_file(manifest: dict, tag_name, output_dir: Path) -> Path:
    """Write manifest dict to a canonical JSON file (JCS / RFC 8785).

    Args:
        manifest: Manifest dict to serialize.
        output_dir: Directory for the file.

    Returns:
        Path to the written file.
    """
    output_file = output_dir / f"manifest-{tag_name}.json"
    canonical = jcs.canonicalize(manifest)

    with open(output_file, "wb") as f:
        f.write(canonical)

    return output_file