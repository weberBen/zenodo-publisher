"""Test: archive formats, hashes, tree hash, and contents.

Standalone test — creates temporary repos with known files
and runs `zp archive` to verify archive behavior.
Each test independently verifies the output (files on disk, hashes, contents).
"""

import subprocess
from pathlib import Path

from tests.utils.cli import ZpRunner
from tests.utils.git import GitClient
from tests.utils.ndjson import find_data
from tests.utils import fs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TAG = "v1.0.0"

MINIMAL_CONFIG = {
    "project_name": {"prefix": "TestProject", "suffix": "-{tag_name}"},
}

REPO_FILES = {
    "README.md": "# Test Project\n",
    "src/main.py": "print('hello')\n",
    "src/utils.py": "def helper(): pass\n",
    ".gitignore": "__pycache__/\n*.pyc\nbuild/\n",
}

PROJECT_NAME = f"TestProject-{TAG}"


def _setup_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a repo with known files, bare remote, and a pushed tag.

    Returns (local_path, output_dir).
    """
    origin = tmp_path / "origin.git"
    local = tmp_path / "local"
    output = tmp_path / "output"
    output.mkdir()

    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True, capture_output=True, text=True,
    )

    git = GitClient.init(local)
    git._run("remote", "add", "origin", str(origin))

    for path, content in REPO_FILES.items():
        git.add_file(path, content)

    git.add_and_commit("initial")
    git.push("origin", "main")

    git.tag_create(TAG)
    git._run("push", "origin", TAG)

    return local, output


def _run_archive(tmp_path, fix_log_path, config_override=None, extra_args=None):
    """Setup repo + run zp archive, return (result, output_dir)."""
    local, output = _setup_repo(tmp_path)
    config = {**MINIMAL_CONFIG, **(config_override or {})}

    runner = ZpRunner(local)
    result = runner.run_test(
        "archive", config=config,
        extra_args=["--tag", TAG, "--output-dir", str(output)] + (extra_args or []),
        log_path=fix_log_path,
        fail_on="ignore",
    )
    return result, output


def _get_archive_data(result):
    """Extract archive_result data event."""
    data = find_data(result.events, "archive_result")
    assert data, f"No archive_result data event. events={result.events}"
    return data


def _compute_tree_hash(content_dir: Path, object_format: str = "sha1") -> str:
    """Compute git tree hash independently from zp."""
    subprocess.run(
        ["git", "init", f"--object-format={object_format}", "."],
        cwd=str(content_dir), check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "add", "--all"],
        cwd=str(content_dir), check=True, capture_output=True, text=True,
    )
    r = subprocess.run(
        ["git", "write-tree"],
        cwd=str(content_dir), check=True, capture_output=True, text=True,
    )
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# Format tests — verify file exists on disk with correct extension
# ---------------------------------------------------------------------------

def test_archive_zip(tmp_path, fix_log_path):
    """archive format zip: file should exist on disk as .zip."""
    result, output = _run_archive(tmp_path, fix_log_path, {"archive": {"format": "zip"}})

    expected = output / f"{PROJECT_NAME}.zip"
    assert expected.exists(), f"Expected {expected} on disk. Contents: {list(output.iterdir())}"
    assert expected.stat().st_size > 0

    data = _get_archive_data(result)
    assert data["format"] == "zip"
    assert Path(data["path"]) == expected


def test_archive_tar(tmp_path, fix_log_path):
    """archive format tar: file should exist on disk as .tar."""
    result, output = _run_archive(tmp_path, fix_log_path, {"archive": {"format": "tar"}})

    expected = output / f"{PROJECT_NAME}.tar"
    assert expected.exists(), f"Expected {expected} on disk. Contents: {list(output.iterdir())}"
    assert expected.stat().st_size > 0

    data = _get_archive_data(result)
    assert data["format"] == "tar"
    assert Path(data["path"]) == expected


def test_archive_tar_gz(tmp_path, fix_log_path):
    """archive format tar.gz: file should exist on disk as .tar.gz."""
    result, output = _run_archive(tmp_path, fix_log_path, {"archive": {"format": "tar.gz"}})

    expected = output / f"{PROJECT_NAME}.tar.gz"
    assert expected.exists(), f"Expected {expected} on disk. Contents: {list(output.iterdir())}"
    assert expected.stat().st_size > 0

    data = _get_archive_data(result)
    assert data["format"] == "tar.gz"
    assert Path(data["path"]) == expected


# ---------------------------------------------------------------------------
# Hash tests — compute hashes independently and compare with zp output
# ---------------------------------------------------------------------------

def test_archive_hash_sha256(tmp_path, fix_log_path):
    """sha256: independently computed hash must match zp output."""
    result, output = _run_archive(tmp_path, fix_log_path, {
        "archive": {"format": "zip"},
        "hash_algorithms": ["sha256"],
    })
    data = _get_archive_data(result)
    archive_path = Path(data["path"])

    assert archive_path.exists()
    local = fs.compute_hash(archive_path, "sha256")
    assert data["hashes"]["sha256"] == local, \
        f"sha256 mismatch: zp={data['hashes']['sha256']}, local={local}"


def test_archive_hash_md5(tmp_path, fix_log_path):
    """md5: independently computed hash must match zp output."""
    result, output = _run_archive(tmp_path, fix_log_path, {
        "archive": {"format": "zip"},
        "hash_algorithms": ["md5"],
    })
    data = _get_archive_data(result)
    archive_path = Path(data["path"])

    assert archive_path.exists()
    local = fs.compute_hash(archive_path, "md5")
    assert data["hashes"]["md5"] == local


def test_archive_hash_sha1(tmp_path, fix_log_path):
    """sha1: independently computed hash must match zp output."""
    result, output = _run_archive(tmp_path, fix_log_path, {
        "archive": {"format": "zip"},
        "hash_algorithms": ["sha1"],
    })
    data = _get_archive_data(result)
    archive_path = Path(data["path"])

    assert archive_path.exists()
    local = fs.compute_hash(archive_path, "sha1")
    assert data["hashes"]["sha1"] == local


def test_archive_hash_sha512(tmp_path, fix_log_path):
    """sha512: independently computed hash must match zp output."""
    result, output = _run_archive(tmp_path, fix_log_path, {
        "archive": {"format": "zip"},
        "hash_algorithms": ["sha512"],
    })
    data = _get_archive_data(result)
    archive_path = Path(data["path"])

    assert archive_path.exists()
    local = fs.compute_hash(archive_path, "sha512")
    assert data["hashes"]["sha512"] == local


def test_archive_hash_multiple(tmp_path, fix_log_path):
    """Multiple algorithms: all independently computed hashes must match."""
    algos = ["md5", "sha1", "sha256", "sha512"]
    result, output = _run_archive(tmp_path, fix_log_path, {
        "archive": {"format": "zip"},
        "hash_algorithms": algos,
    })
    data = _get_archive_data(result)
    archive_path = Path(data["path"])

    assert archive_path.exists()
    for algo in algos:
        local = fs.compute_hash(archive_path, algo)
        assert data["hashes"][algo] == local, \
            f"{algo} mismatch: zp={data['hashes'][algo]}, local={local}"


def test_archive_hash_tar_gz(tmp_path, fix_log_path):
    """Hashes on tar.gz: verify independently on the actual .tar.gz file."""
    result, output = _run_archive(tmp_path, fix_log_path, {
        "archive": {"format": "tar.gz"},
        "hash_algorithms": ["md5", "sha256"],
    })
    data = _get_archive_data(result)
    archive_path = Path(data["path"])

    assert archive_path.exists()
    assert archive_path.name.endswith(".tar.gz")
    for algo in ("md5", "sha256"):
        local = fs.compute_hash(archive_path, algo)
        assert data["hashes"][algo] == local, \
            f"{algo} mismatch on tar.gz: zp={data['hashes'][algo]}, local={local}"


# ---------------------------------------------------------------------------
# Tree hash tests — extract archive, compute git tree hash independently
# ---------------------------------------------------------------------------

def test_archive_tree_hash(tmp_path, fix_log_path):
    """tree (sha1 object format): extract archive, git write-tree, compare."""
    result, output = _run_archive(tmp_path, fix_log_path, {
        "archive": {"format": "zip"},
        "hash_algorithms": ["sha256", "tree"],
    })
    data = _get_archive_data(result)
    assert "tree" in data["hashes"], f"No tree hash in result: {data['hashes']}"

    archive_path = Path(data["path"])
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    content_dir = fs.extract_archive(archive_path, extract_dir)

    local_tree = _compute_tree_hash(content_dir, object_format="sha1")
    assert data["hashes"]["tree"] == local_tree, \
        f"tree hash mismatch: zp={data['hashes']['tree']}, local={local_tree}"


def test_archive_tree256_hash(tmp_path, fix_log_path):
    """tree256 (sha256 object format): extract archive, git write-tree, compare."""
    result, output = _run_archive(tmp_path, fix_log_path, {
        "archive": {"format": "zip"},
        "hash_algorithms": ["sha256", "tree256"],
    })
    data = _get_archive_data(result)
    assert "tree256" in data["hashes"], f"No tree256 hash in result: {data['hashes']}"

    archive_path = Path(data["path"])
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    content_dir = fs.extract_archive(archive_path, extract_dir)

    local_tree = _compute_tree_hash(content_dir, object_format="sha256")
    assert data["hashes"]["tree256"] == local_tree, \
        f"tree256 hash mismatch: zp={data['hashes']['tree256']}, local={local_tree}"


def test_archive_tree_hash_tar(tmp_path, fix_log_path):
    """tree hash on tar format: should still match extracted content."""
    result, output = _run_archive(tmp_path, fix_log_path, {
        "archive": {"format": "tar"},
        "hash_algorithms": ["tree"],
    })
    data = _get_archive_data(result)
    assert "tree" in data["hashes"]

    archive_path = Path(data["path"])
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    content_dir = fs.extract_archive(archive_path, extract_dir)

    local_tree = _compute_tree_hash(content_dir, object_format="sha1")
    assert data["hashes"]["tree"] == local_tree


# ---------------------------------------------------------------------------
# Content tests — verify actual archive contents on disk
# ---------------------------------------------------------------------------

def test_archive_contains_expected_files(tmp_path, fix_log_path):
    """Archive should contain all committed files."""
    result, output = _run_archive(tmp_path, fix_log_path, {"archive": {"format": "zip"}})
    data = _get_archive_data(result)

    archive_path = Path(data["path"])
    assert archive_path.exists()

    # Extract and verify files exist on disk
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    content_dir = fs.extract_archive(archive_path, extract_dir)

    for rel_path, expected_content in REPO_FILES.items():
        extracted_file = content_dir / rel_path
        assert extracted_file.exists(), \
            f"{rel_path} missing from archive. Extracted: {list(content_dir.rglob('*'))}"
        assert extracted_file.read_text() == expected_content, \
            f"{rel_path} content mismatch"


def test_archive_contents_tar_gz(tmp_path, fix_log_path):
    """tar.gz archive should also contain all committed files with correct content."""
    result, output = _run_archive(tmp_path, fix_log_path, {"archive": {"format": "tar.gz"}})
    data = _get_archive_data(result)

    archive_path = Path(data["path"])
    assert archive_path.exists()

    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    content_dir = fs.extract_archive(archive_path, extract_dir)

    for rel_path, expected_content in REPO_FILES.items():
        extracted_file = content_dir / rel_path
        assert extracted_file.exists(), \
            f"{rel_path} missing from tar.gz. Extracted: {list(content_dir.rglob('*'))}"
        assert extracted_file.read_text() == expected_content


def test_archive_excludes_gitignored(tmp_path, fix_log_path):
    """Files matching .gitignore should NOT be in the archive."""
    local, output = _setup_repo(tmp_path)

    # Create gitignored files (not committed, git archive won't include them)
    (local / "__pycache__").mkdir()
    (local / "__pycache__" / "main.cpython-310.pyc").write_bytes(b"\x00")
    (local / "build").mkdir()
    (local / "build" / "output.bin").write_bytes(b"\x00")

    config = {**MINIMAL_CONFIG, "archive": {"format": "zip"}}
    runner = ZpRunner(local)
    result = runner.run_test(
        "archive", config=config,
        extra_args=["--tag", TAG, "--output-dir", str(output)],
        log_path=fix_log_path,
        fail_on="ignore",
    )
    data = _get_archive_data(result)

    # Extract and verify gitignored files are absent
    archive_path = Path(data["path"])
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    content_dir = fs.extract_archive(archive_path, extract_dir)

    all_files = [f.name for f in content_dir.rglob("*") if f.is_file()]
    assert "main.cpython-310.pyc" not in all_files, \
        f".pyc file should be excluded. Files: {all_files}"
    assert "output.bin" not in all_files, \
        f"build/ file should be excluded. Files: {all_files}"

    # But committed files should still be there
    assert (content_dir / "README.md").exists()
    assert (content_dir / "src" / "main.py").exists()
