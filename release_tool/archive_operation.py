import shutil
import hashlib
import tempfile
from pathlib import Path

from .git_operations import archive_project
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



def compute_md5(file_path: Path) -> str:
    """Compute MD5 checksum of a file."""
    md5_hash = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()


def compute_sha256(file_path: Path) -> str:
    """Compute SHA256 checksum of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()

def _compute_file_hash(file_path: Path, algorithm: str) -> str:
    """Compute hash of a file using the given hashlib algorithm."""
    h = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _compute_identifiers(config, results) -> list | None:
    """Compute identifier hashes from selected archived files for each configured algorithm.

    For each algorithm, if multiple files match, their hashes are sorted and concatenated,
    then hashed again to produce a single deterministic value.

    Returns a list of identifier dicts, one per algorithm, or None if no files match.
    """
    id_types = set(config.zenodo_identifier_types)

    matching_entries = [
        entry for entry in results
        if (entry["extension"] in id_types) or (entry["type"] in id_types)
    ]

    if not matching_entries:
        return None

    identifiers = []
    for algorithm in config.zenodo_identifier_hash_algorithms:
        # Reuse pre-computed hash if available, otherwise compute on the fly
        file_hashes = [
            entry.get(algorithm) or _compute_file_hash(entry["file_path"], algorithm)
            for entry in matching_entries
        ]

        if len(file_hashes) == 1:
            identifier_hash = file_hashes[0]
        else:
            combined = "".join(sorted(file_hashes))
            identifier_hash = hashlib.new(algorithm, combined.encode()).hexdigest()

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
            "md5": compute_md5(file_path),
            "sha256": compute_sha256(file_path),
            "is_preview": is_preview,
            "filename": filename,
            "extension": extension,
            "type": "main_file",
            "persist": persist_file,
            "is_signature": False,
        })

    if "project" in config.archive_types:
        persist_file = "project" in config.persist_types
        file_path, filename, extension = archive_project(
            config.project_root,
            tag_name,
            config.project_name,
            archive_dir=config.archive_dir,
            persist=persist_file
        )
        is_preview = (config.main_file_extension == extension)
        results.append({
            "file_path": file_path,
            "md5": compute_md5(file_path),
            "sha256": compute_sha256(file_path),
            "is_preview": is_preview,
            "filename": filename,
            "extension": extension,
            "type": "project",
            "persist": persist_file,
            "is_signature": False,
        })

    identifiers = None
    if config.zenodo_identifier_hash and config.zenodo_identifier_types:
        identifiers = _compute_identifiers(config, results)

    return results, identifiers