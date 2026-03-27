"""File utilities shared across pipelines."""

import shutil
from pathlib import Path

from . import output


def persist_files(entries: list, archive_dir: Path | None, tag_name: str) -> None:
    """Move files marked as persist to archive_dir/tag_name.

    If files already exist at the destination, lists them first then
    prompts the user one-by-one with an option to apply the choice
    to all remaining files.

    Updates each entry's file_path in-place after moving.

    Args:
        entries: List of entry dicts with file_path, persist, etc.
        archive_dir: Base directory for persistent archives (None = skip).
        tag_name: Tag name used as subdirectory.
    """
    if not archive_dir:
        return

    to_persist = [e for e in entries if e.get("persist")]
    if not to_persist:
        return

    persist_dir = archive_dir / tag_name
    persist_dir.mkdir(parents=True, exist_ok=True)

    # Check which files already exist
    existing = [e for e in to_persist if (persist_dir / e["file_path"].name).exists()]
    if existing:
        output.info(f"Files already exist in {persist_dir}:")
        for e in existing:
            output.detail(f"  • {e['file_path'].name}")

    confirm = output.ConfirmPrompt(
        [output.YES, output.NO, output.YES_ALL, output.NO_ALL],
        level="light",
    )
    apply_all = None  # None = ask each time, True = overwrite all, False = skip all
    for entry in to_persist:
        src = entry["file_path"]
        dst = persist_dir / src.name

        if dst.exists():
            if apply_all is not None:
                overwrite = apply_all
            else:
                result = confirm.ask(f"Overwrite {dst.name}?")
                if result.name == "yall":
                    overwrite = True
                    apply_all = True
                elif result.name == "nall":
                    overwrite = False
                    apply_all = False
                else:
                    overwrite = result.is_accept

            if not overwrite:
                output.detail(f"Skipped {dst.name}")
                continue

        shutil.move(str(src), str(dst))
        entry["file_path"] = dst
        output.detail(f"Persisted {dst.name} → {persist_dir}")
