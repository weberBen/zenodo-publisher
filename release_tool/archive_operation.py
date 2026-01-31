import shutil
import hashlib
import tempfile
from pathlib import Path

from .git_operations import archive_project


def archive_pdf(config, tag_name: str, persist: bool = True) -> Path:
    """
    Copy main.pdf to {base_name}-{tag_name}.pdf.

    Args:
        config: Configuration object
        tag_name: Tag name (version)
        persist: If True, save to archive_dir; if False, create temp file

    Returns:
        Path to the PDF file

    Raises:
        FileNotFoundError: If main.pdf doesn't exist
    """
    latex_dir = Path(config.latex_dir)
    main_pdf = latex_dir / f"{config.pdf_base_name}.pdf"

    if not main_pdf.exists():
        raise FileNotFoundError(
            f"main.pdf not found at {main_pdf}\n"
            f"Make sure LaTeX build completed successfully"
        )

    new_name = f"{config.base_name}-{tag_name}.pdf"

    if persist and config.archive_dir:
        new_pdf = config.archive_dir / new_name
    else:
        with tempfile.NamedTemporaryFile(suffix=".pdf", prefix=f"{config.base_name}-{tag_name}_", delete=False) as f:
            new_pdf = Path(f.name)

    print(f"\n📝 Copying PDF: {config.pdf_base_name}.pdf → {new_pdf}")
    shutil.copy(main_pdf, new_pdf)
    print(f"✓ PDF copied to {new_pdf}")

    return new_pdf



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

    if "pdf" in config.archive_types:
        persist_pdf = "pdf" in config.persist_types
        pdf_path = archive_pdf(config, tag_name, persist=persist_pdf)
        results.append((pdf_path, compute_md5(pdf_path)))

    if "project" in config.archive_types:
        persist_project = "project" in config.persist_types
        zip_path = archive_project(
            config.project_root,
            tag_name,
            config.base_name,
            archive_dir=config.archive_dir,
            persist=persist_project
        )
        results.append((zip_path, compute_md5(zip_path)))

    return results