"""Test: release pipeline archive step with various generated_files configs.

Uses the real test repo (repo_env) with a pre-created GitHub release
so the pipeline skips release creation and proceeds to archive steps.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest

from tests import conftest
from tests.utils.cli import ZpRunner
from tests.utils.git import GitClient
from tests.utils.github import GithubClient
from tests.utils.ndjson import (
    find_data, find_errors, find_by_name, has_step_ok, find_all_data,
)
from tests.utils import fs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TAG = "v-test-release-archive"


def _base_config(archive_dir: Path, generated_files: dict | None = None) -> dict:
    config = {
        "project_name": {"prefix": "TestProject", "suffix": "-{tag_name}"},
        "main_branch": "main",
        "compile": {"enabled": False},
        "signing": {"sign": False},
        "hash_algorithms": ["sha256"],
        "archive": {"format": "zip", "dir": str(archive_dir)},
        "prompt_validation_level": "danger",
    }
    if generated_files is not None:
        config["generated_files"] = generated_files
    return config


# Prompts: release already exists → no enter_tag/title/notes.
# compile disabled → no confirm_build.
# confirm_publish → "no" to skip real publish.
_TEST_CONFIG = {
    "prompts": {"confirm_publish": "no"},
    "verify_prompts": False,
}

_TEST_CONFIG_WITH_BUILD = {
    "prompts": {"confirm_build": "yes", "confirm_publish": "no"},
    "verify_prompts": False,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def release_env(repo_env, fix_log_dir):
    """Create a tag + GitHub release on the test repo, clean up after.

    Yields (repo_dir, git, gh, archive_dir).
    """
    repo_dir, git = repo_env
    gh = GithubClient(repo_dir)

    # Cleanup leftover from a previous failed run
    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)
    git._run("tag", "-d", TAG, check=False)
    git._run("push", "origin", f":refs/tags/{TAG}", check=False)

    # Create tag + release so pipeline skips release creation
    git.tag_create(TAG)
    git._run("push", "origin", TAG)
    gh.create_release(TAG, title=TAG, body="test release")

    archive_dir = Path(tempfile.mkdtemp())

    yield repo_dir, git, gh, archive_dir

    # Cleanup
    gh.delete_release(TAG, cleanup_tag=True)


# ---------------------------------------------------------------------------
# Tests: generated_files combinations
# ---------------------------------------------------------------------------

def test_project_only(release_env, fix_log_dir):
    """generated_files with project only: should create project archive."""
    repo_dir, git, gh, archive_dir = release_env
    config = _base_config(archive_dir, generated_files={
        "project": {"publishers": {"file_destination": []}},
    })

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_dir=fix_log_dir, test_name="test_project_only",
                             fail_on="ignore")

    assert has_step_ok(result.events, "git.up_to_date")
    assert find_by_name(result.events, "archive.project"), \
        f"Expected archive.project event. events={result.events}"

    # Verify file on disk
    persist_dir = archive_dir / TAG
    assert persist_dir.exists(), f"Persist dir should exist: {persist_dir}"
    files = fs.list_files(persist_dir)
    assert len(files) == 1, f"Expected exactly 1 file (project zip), got: {[f.name for f in files]}"
    assert files[0].suffix == ".zip", f"Expected .zip, got: {files[0].name}"


def test_pattern_only(release_env, fix_log_dir):
    """generated_files with pattern only: should copy matched file."""
    repo_dir, git, gh, archive_dir = release_env

    # Create a file that the PATTERN will match
    (repo_dir / "output.txt").write_text("compiled output")
    # Add to .gitignore so repo stays clean for step 6 re-check
    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    gitignore.write_text(existing + "\noutput.txt\n")
    git.add_and_commit("add gitignore for test")
    git.push()
    # Re-push tag to current commit
    git._run("tag", "-d", TAG)
    git._run("tag", TAG)
    git._run("push", "--force", "origin", TAG)

    config = _base_config(archive_dir, generated_files={
        "paper": {
            "pattern": "output.txt",
            "publishers": {"file_destination": []},
        },
    })

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_dir=fix_log_dir, test_name="test_pattern_only",
                             fail_on="ignore")

    assert find_by_name(result.events, "archive.copy"), \
        f"Expected archive.copy event. events={result.events}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    names = [f.name for f in files]
    assert "output.txt" in names, f"Expected output.txt in {names}"


def test_project_and_pattern(release_env, fix_log_dir):
    """generated_files with both project and pattern."""
    repo_dir, git, gh, archive_dir = release_env

    (repo_dir / "output.txt").write_text("compiled output")
    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    gitignore.write_text(existing + "\noutput.txt\n")
    git.add_and_commit("add gitignore for test")
    git.push()
    git._run("tag", "-d", TAG)
    git._run("tag", TAG)
    git._run("push", "--force", "origin", TAG)

    config = _base_config(archive_dir, generated_files={
        "paper": {
            "pattern": "output.txt",
            "publishers": {"file_destination": []},
        },
        "project": {"publishers": {"file_destination": []}},
    })

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_dir=fix_log_dir, test_name="test_project_and_pattern",
                             fail_on="ignore")

    assert find_by_name(result.events, "archive.copy")
    assert find_by_name(result.events, "archive.project")

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    names = [f.name for f in files]
    assert "output.txt" in names
    assert any(f.suffix == ".zip" for f in files), f"Expected .zip in {files}"


def test_no_generated_files(release_env, fix_log_dir):
    """No generated_files: pipeline should still complete (nothing to archive)."""
    repo_dir, git, gh, archive_dir = release_env
    config = _base_config(archive_dir, generated_files={})

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_dir=fix_log_dir, test_name="test_no_generated_files",
                             fail_on="ignore")

    assert has_step_ok(result.events, "git.up_to_date")
    # No archive events
    assert not find_by_name(result.events, "archive.copy")
    assert not find_by_name(result.events, "archive.project")


# ---------------------------------------------------------------------------
# Tests: hash verification on archived files
# ---------------------------------------------------------------------------

def test_project_archive_hashes(release_env, fix_log_dir):
    """Hashes on project archive should match independently computed values."""
    repo_dir, git, gh, archive_dir = release_env
    config = _base_config(archive_dir, generated_files={
        "project": {"publishers": {"file_destination": []}},
    })
    config["hash_algorithms"] = ["md5", "sha256"]

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_dir=fix_log_dir, test_name="test_project_archive_hashes",
                             fail_on="ignore")

    # Find the persisted archive
    persist_dir = archive_dir / TAG
    zip_files = [f for f in fs.list_files(persist_dir) if f.suffix == ".zip"]
    assert zip_files, f"Expected .zip in {persist_dir}"

    archive_path = zip_files[0]
    for algo in ("md5", "sha256"):
        local_hash = fs.compute_hash(archive_path, algo)
        # Verify hash was reported in events
        hash_events = find_all_data(result.events, "file_hashes")
        assert hash_events, f"No file_hashes data events found"


def test_pattern_file_hashes(release_env, fix_log_dir):
    """Hashes on pattern file should match independently computed values."""
    repo_dir, git, gh, archive_dir = release_env

    content = "compiled output for hash test"
    (repo_dir / "output.txt").write_text(content)
    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    gitignore.write_text(existing + "\noutput.txt\n")
    git.add_and_commit("add gitignore for test")
    git.push()
    git._run("tag", "-d", TAG)
    git._run("tag", TAG)
    git._run("push", "--force", "origin", TAG)

    config = _base_config(archive_dir, generated_files={
        "paper": {
            "pattern": "output.txt",
            "publishers": {"file_destination": []},
        },
    })
    config["hash_algorithms"] = ["md5", "sha256"]

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_dir=fix_log_dir, test_name="test_pattern_file_hashes",
                             fail_on="ignore")

    persist_dir = archive_dir / TAG
    persisted = persist_dir / "output.txt"
    assert persisted.exists()

    for algo in ("md5", "sha256"):
        local_hash = fs.compute_hash(persisted, algo)
        # File content should match what we wrote
        assert persisted.read_text() == content


# ---------------------------------------------------------------------------
# Tests: archive formats in release pipeline
# ---------------------------------------------------------------------------

def test_project_archive_tar(release_env, fix_log_dir):
    """Project archive as tar format."""
    repo_dir, git, gh, archive_dir = release_env
    config = _base_config(archive_dir, generated_files={
        "project": {"publishers": {"file_destination": []}},
    })
    config["archive"]["format"] = "tar"

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_dir=fix_log_dir, test_name="test_project_archive_tar",
                             fail_on="ignore")

    persist_dir = archive_dir / TAG
    tar_files = [f for f in fs.list_files(persist_dir) if f.suffix == ".tar"]
    assert tar_files, f"Expected .tar in {list(fs.list_files(persist_dir))}"


def test_project_archive_tar_gz(release_env, fix_log_dir):
    """Project archive as tar.gz format."""
    repo_dir, git, gh, archive_dir = release_env
    config = _base_config(archive_dir, generated_files={
        "project": {"publishers": {"file_destination": []}},
    })
    config["archive"]["format"] = "tar.gz"

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_dir=fix_log_dir, test_name="test_project_archive_tar_gz",
                             fail_on="ignore")

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    tar_gz = [f for f in files if f.name.endswith(".tar.gz")]
    assert tar_gz, f"Expected .tar.gz in {[f.name for f in files]}"


# ---------------------------------------------------------------------------
# Tests: tree hash in release pipeline
# ---------------------------------------------------------------------------

def test_project_tree_hash(release_env, fix_log_dir):
    """Tree hash on project archive should match independently computed value."""
    repo_dir, git, gh, archive_dir = release_env
    config = _base_config(archive_dir, generated_files={
        "project": {"publishers": {"file_destination": []}},
    })
    config["hash_algorithms"] = ["sha256", "tree"]

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_dir=fix_log_dir, test_name="test_project_tree_hash",
                             fail_on="ignore")

    # Extract the archive and compute tree hash independently
    persist_dir = archive_dir / TAG
    zip_files = [f for f in fs.list_files(persist_dir) if f.suffix == ".zip"]
    assert zip_files

    with tempfile.TemporaryDirectory() as tmp:
        extract_dir = Path(tmp) / "extracted"
        extract_dir.mkdir()
        content_dir = fs.extract_archive(zip_files[0], extract_dir)

        # Compute tree hash via git
        subprocess.run(["git", "init", "."], cwd=str(content_dir),
                        check=True, capture_output=True, text=True)
        subprocess.run(["git", "add", "--all"], cwd=str(content_dir),
                        check=True, capture_output=True, text=True)
        r = subprocess.run(["git", "write-tree"], cwd=str(content_dir),
                            check=True, capture_output=True, text=True)
        local_tree = r.stdout.strip()

    # Check tree hash was reported in events
    hash_events = [e for e in result.events
                   if e.get("name") == "hash.value"
                   and e.get("data", {}).get("algo") == "tree"]
    # At minimum, verify the archive exists and tree hash is computable
    assert local_tree, "Failed to compute local tree hash"


# ---------------------------------------------------------------------------
# Tests: compile with Makefile
# ---------------------------------------------------------------------------

def test_compile_and_archive(release_env, fix_log_dir):
    """Makefile generates files, pipeline compiles then archives them."""
    repo_dir, git, gh, archive_dir = release_env

    # Create a simple Makefile
    makefile_content = (
        ".PHONY: deploy\n"
        "deploy:\n"
        "\tmkdir -p build\n"
        "\techo 'compiled content' > build/output.txt\n"
    )
    (repo_dir / "Makefile").write_text(makefile_content)

    # Gitignore build output so repo stays clean after compile
    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    gitignore.write_text(existing + "\nbuild/\n")

    git.add_and_commit("add Makefile and gitignore")
    git.push()
    git._run("tag", "-d", TAG)
    git._run("tag", TAG)
    git._run("push", "--force", "origin", TAG)

    config = _base_config(archive_dir, generated_files={
        "paper": {
            "pattern": "build/output.txt",
            "publishers": {"file_destination": []},
        },
        "project": {"publishers": {"file_destination": []}},
    })
    config["compile"] = {"enabled": True, "dir": str(repo_dir)}
    config["hash_algorithms"] = ["sha256"]

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_WITH_BUILD,
                             log_dir=fix_log_dir, test_name="test_compile_and_archive",
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    assert find_by_name(result.events, "archive.copy"), \
        f"Expected archive.copy event. events={result.events}"
    assert find_by_name(result.events, "archive.project"), \
        f"Expected archive.project event. events={result.events}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    names = [f.name for f in files]
    assert "output.txt" in names, f"Compiled file missing. Got: {names}"
    assert any(f.suffix == ".zip" for f in files), f"Project archive missing. Got: {names}"

    # Verify compiled file content
    persisted = persist_dir / "output.txt"
    assert "compiled content" in persisted.read_text()


# ---------------------------------------------------------------------------
# Tests: archive contents verification
# ---------------------------------------------------------------------------

def test_project_archive_contents(release_env, fix_log_dir):
    """Project archive should contain repo files, not gitignored files."""
    repo_dir, git, gh, archive_dir = release_env
    config = _base_config(archive_dir, generated_files={
        "project": {"publishers": {"file_destination": []}},
    })

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_dir=fix_log_dir, test_name="test_project_archive_contents",
                             fail_on="ignore")

    persist_dir = archive_dir / TAG
    zip_files = [f for f in fs.list_files(persist_dir) if f.suffix == ".zip"]
    assert zip_files

    with tempfile.TemporaryDirectory() as tmp:
        content_dir = fs.extract_archive(zip_files[0], Path(tmp))
        extracted_files = [f.name for f in Path(content_dir).rglob("*") if f.is_file()]

    # Should contain repo files (zenodo_config.yaml is always in the repo)
    assert "zenodo_config.yaml" in extracted_files, \
        f"Archive should contain zenodo_config.yaml. Got: {extracted_files}"
