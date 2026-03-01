import os
import shutil
import hashlib
import tempfile
from pathlib import Path

from .git_operations import archive_zip_project, extract_zip, compute_tree_hash, pack_tar
from .config_transform_common import TREE_ALGORITHMS
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



def compute_file_hash(file_path: Path, algorithm: str) -> dict:
    """Compute hash of a file. Returns {"value": hex, "formatted_value": "algo:hex"}."""
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    hex_value = h.hexdigest()
    return {"value": hex_value, "formatted_value": f"{algorithm}:{hex_value}"}


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
                hashes[algo] = {"value": value, "formatted_value": f"{algo}:{value}"}
            else:
                # hashlib algo (e.g. sha1) but label with tree algo name
                raw = compute_file_hash(entry["file_path"], TREE_ALGORITHMS[algo])
                hashes[algo] = {"value": raw["value"], "formatted_value": f"{algo}:{raw['value']}"}

        entry["hashes"] = hashes


def _compute_identifiers(config, results) -> list | None:
    """Build identifier dicts from pre-computed hashes.

    For each algorithm, if multiple files match, their hashes are sorted and concatenated,
    then hashed again to produce a single deterministic value.

    All hashes must already be present in each entry dict (computed by _compute_hashes).
    """
    id_types = set(config.zenodo_identifier_types)

    matching_entries = [
        entry for entry in results
        if (entry["extension"] in id_types) or (entry["type"] in id_types)
    ]

    if not matching_entries:
        return None

    identifiers = []
    for algorithm in config.hash_algorithms:
        file_hashes = [entry["hashes"][algorithm]["value"] for entry in matching_entries]

        if len(file_hashes) == 1:
            identifier_hash = file_hashes[0]
        else:
            hashlib_algo = TREE_ALGORITHMS.get(algorithm, algorithm)
            combined = "".join(sorted(file_hashes))
            identifier_hash = hashlib.new(hashlib_algo, combined.encode()).hexdigest()

        identifiers.append({
            "value": identifier_hash,
            "formatted_value": f"{algorithm}:{identifier_hash}",
            "type": algorithm,
            "files": file_hashes,
            "description": "sorted by hash value",
        })

    return identifiers


def archive(config, tag_name: str) -> tuple[list, list | None]:
    """
    Create archives, compute checksums, and optionally compute identifier hashes.

    Uses config.archive_types to determine what to archive (pdf, project).
    Uses config.persist_types to determine what to persist to archive_dir.

    Returns:
        Tuple of (archived_files list, identifiers list or None)
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

    identifiers = _postprocess(config, results)

    return results, identifiers


def _postprocess(config, results):
    """Process project archive (tree hashes, TAR), compute all hashes, build identifiers."""
    hash_algos = config.hash_algorithms if (
        config.zenodo_identifier_hash and config.zenodo_identifier_types
    ) else []

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

    identifiers = None
    if config.zenodo_identifier_hash and config.zenodo_identifier_types:
        identifiers = _compute_identifiers(config, results)

    return identifiers