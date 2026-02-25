import shutil
import hashlib
import tempfile
from pathlib import Path

from .git_operations import archive_project


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

    print(f"\n📝 Copying Preview file: {main_file.name} → {new_file}")
    shutil.copy(main_file, new_file)
    print(f"✓ File copied to {new_file}")

    return new_file, filename, extension



def compute_md5(file_path: Path) -> str:
    """Compute MD5 checksum of a file."""
    md5_hash = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()


def archive(config, tag_name: str) -> list[tuple[Path, str]]:
    """
    Create archives and compute their MD5 checksums.

    Uses config.archive_types to determine what to archive (pdf, project).
    Uses config.persist_types to determine what to persist to archive_dir.

    Args:
        config: Configuration object
        tag_name: Tag name (version)

    Returns:
        List of tuples (file_path, md5_checksum)
    """
    results = []

    if config.main_file_extension in config.archive_types:
        persist_file = config.main_file_extension in config.persist_types
        file_path, filename, extension = archive_preview_file(config, tag_name, persist=persist_file)
        is_preview = (config.main_file_extension == extension)
        results.append({
            "file_path": file_path, "md5": compute_md5(file_path),
            "is_preview": is_preview, "filename": filename,
            "persist": persist_file, "is_signature": False,
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
            "file_path": file_path, "md5": compute_md5(file_path),
            "is_preview": is_preview, "filename": filename,
            "persist": persist_file, "is_signature": False,
        })

    return results