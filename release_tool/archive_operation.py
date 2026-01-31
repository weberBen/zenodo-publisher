import shutil
from pathlib import Path

def archive_pdf(config, tag_name: str) -> Path:
    """
    Rename main.pdf to {base_name}-{tag_name}.pdf.

    Args:
        latex_dir: Path to LaTeX directory
        base_name: Base name for the PDF
        tag_name: Tag name (version)

    Returns:
        Path to renamed PDF file

    Raises:
        FileNotFoundError: If main.pdf doesn't exist
    """
    # Convertir en Path
    latex_dir = Path(config.latex_dir)
    archive_dir = Path(config.archive_dir)
    
    main_pdf = latex_dir / f"{config.pdf_base_name}.pdf"
    if not main_pdf.exists():
        raise FileNotFoundError(
            f"main.pdf not found at {main_pdf}\n"
            f"Make sure LaTeX build completed successfully"
        )

    new_name = f"{config.base_name}-{tag_name}.pdf"
    new_pdf = archive_dir / new_name  # ✅ Maintenant c'est un Path

    print(f"\n📝 Renaming PDF: main.pdf → {new_name}")
    shutil.copy(main_pdf, new_pdf)
    print(f"✓ PDF renamed to {new_name}")

    return new_pdf
    