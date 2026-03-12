"""Filesystem utilities for E2E tests.

Hash computation, archive inspection, file operations.
Independent from release_tool.
"""

import hashlib
import json
import shutil
import tempfile
import zipfile
import tarfile
from pathlib import Path


# --- Hashing ---

def compute_hash(filepath: Path, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_hashes(filepath: Path, algos: list[str] | None = None) -> dict[str, str]:
    algos = algos or ["md5", "sha256"]
    return {algo: compute_hash(filepath, algo) for algo in algos}


# --- Archive inspection ---

def list_archive_contents(archive_path: Path) -> list[str]:
    """List all file names inside an archive."""
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path) as zf:
            return zf.namelist()
    elif archive_path.name.endswith(".tar.gz"):
        with tarfile.open(archive_path, "r:gz") as tf:
            return tf.getnames()
    elif archive_path.suffix == ".tar":
        with tarfile.open(archive_path, "r:") as tf:
            return tf.getnames()
    raise ValueError(f"Unknown archive format: {archive_path}")


def extract_archive(archive_path: Path, dest_dir: Path) -> Path:
    """Extract an archive to dest_dir. Returns the extracted root directory."""
    if archive_path.suffix == ".zip":
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(dest_dir)
    elif archive_path.name.endswith(".tar.gz"):
        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(dest_dir)
    elif archive_path.suffix == ".tar":
        with tarfile.open(archive_path, "r:") as tf:
            tf.extractall(dest_dir)
    else:
        raise ValueError(f"Unknown archive format: {archive_path}")

    # Return first directory (usually the archive prefix)
    entries = list(dest_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest_dir


def assert_archive_contains(archive_path: Path, expected_files: list[str]):
    """Assert that an archive contains the expected files (by name, any prefix)."""
    contents = list_archive_contents(archive_path)
    basenames = [Path(c).name for c in contents]
    for expected in expected_files:
        assert expected in basenames, (
            f"'{expected}' not found in archive. Contents: {basenames}"
        )


def compare_archives(path1: Path, path2: Path) -> bool:
    """Compare two archives by extracting and diffing their contents."""
    with tempfile.TemporaryDirectory() as tmp:
        d1 = extract_archive(path1, Path(tmp) / "a")
        d2 = extract_archive(path2, Path(tmp) / "b")
        return _dir_equal(d1, d2)


def _dir_equal(d1: Path, d2: Path) -> bool:
    files1 = sorted(f.relative_to(d1) for f in d1.rglob("*") if f.is_file())
    files2 = sorted(f.relative_to(d2) for f in d2.rglob("*") if f.is_file())
    if files1 != files2:
        return False
    for rel in files1:
        if (d1 / rel).read_bytes() != (d2 / rel).read_bytes():
            return False
    return True


# --- File listing ---

def list_files(directory: Path, recursive: bool = True) -> list[Path]:
    if recursive:
        return [f for f in directory.rglob("*") if f.is_file()]
    return [f for f in directory.iterdir() if f.is_file()]


def move_to_tmpdir(files: list[Path]) -> tuple[Path, list[Path]]:
    """Move files to a temp dir. Returns (tmpdir_path, new_paths)."""
    tmpdir = Path(tempfile.mkdtemp())
    new_paths = []
    for f in files:
        dst = tmpdir / f.name
        shutil.move(str(f), str(dst))
        new_paths.append(dst)
    return tmpdir, new_paths


# --- Manifest ---

def parse_manifest(manifest_path: Path) -> dict:
    with open(manifest_path) as f:
        return json.load(f)
