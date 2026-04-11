"""Test: pattern resolution, matching, overlap detection, and multi-match.

Config-level tests (overlap) use tmp_path repos.
Pipeline-level tests (pattern matching, manifest) use the real test repo.
"""

import subprocess
import tempfile
from pathlib import Path

import pytest

from tests import conftest
from tests.utils.cli import ZpRunner
from tests.utils.git import GitClient
from tests.utils.github import GithubClient
from tests.utils.ndjson import find_by_name, find_data, find_errors, has_step_ok
from tests.utils import fs


# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------

TAG = "v-test-pattern"

RELEASE_PROMPTS = {
    "enter_tag": TAG,
    "release_title": "",
    "release_notes": "",
    "confirm_build": "yes",
    "confirm_publish": "no",
    "confirm_persist_overwrite": "yes",
    "confirm_gpg_key": "yes",
}

_TEST_CONFIG = {"prompts": RELEASE_PROMPTS, "verify_prompts": False}


def _base_config(archive_dir: Path = None, **overrides) -> dict:
    config = {
        "project_name": {"prefix": "TestProject", "suffix": "-{tag_name}"},
        "main_branch": "main",
        "compile": {"enabled": False},
        "signing": {"sign": False},
        "hash_algorithms": ["sha256"],
        "prompt_validation_level": "danger",
    }
    if archive_dir:
        config["archive"] = {"format": "zip", "dir": str(archive_dir)}
    config.update(overrides)
    return config


def _setup_tmp_repo(tmp_path: Path) -> tuple[Path, GitClient]:
    """Create a tmp repo with bare remote for config-level tests (no GitHub)."""
    origin = tmp_path / "origin.git"
    local = tmp_path / "local"

    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True, capture_output=True, text=True,
    )

    git = GitClient.init(local)
    git._run("remote", "add", "origin", str(origin))
    git.add_file(".gitkeep", "")
    git.add_and_commit("init")
    git.push("origin", "main")

    return local, git


# ---------------------------------------------------------------------------
# Fixtures (real repo for pipeline-level tests)
# ---------------------------------------------------------------------------

@pytest.fixture
def pattern_env(repo_env, fix_gpg_uid):
    """Yield (repo_dir, git, gh, archive_dir, gpg_uid). Cleanup release after."""
    repo_dir, git = repo_env
    gh = GithubClient(repo_dir)

    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)
    git._run("tag", "-d", TAG, check=False)
    git._run("push", "origin", f":refs/tags/{TAG}", check=False)

    archive_dir = Path(tempfile.mkdtemp())

    yield repo_dir, git, gh, archive_dir, fix_gpg_uid

    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)


def _create_pattern_files(repo_dir, git, files: dict[str, str]):
    """Create files and gitignore them so repo stays clean."""
    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    for path, content in files.items():
        full = repo_dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        if path not in existing:
            existing += f"\n{path}"
    gitignore.write_text(existing + "\n")
    git.add_and_commit("add pattern files")
    git.push("origin", "main")


# ---------------------------------------------------------------------------
# Config-level tests: pattern overlap (tmp_path, no GitHub needed)
# ---------------------------------------------------------------------------

def test_pattern_overlap_identical(tmp_path, fix_log_path):
    """Two identical patterns: should be rejected at config."""
    local, git = _setup_tmp_repo(tmp_path)

    config = _base_config(
        generated_files={
            "a": {"pattern": "*.pdf", "publishers": {"destination": {"file": []}}},
            "b": {"pattern": "*.pdf", "publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(local)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    assert find_by_name(result.events, "config_error.loading.config.generated_files.pattern_overlap"), \
        f"Expected pattern_overlap. events={result.events}"


def test_pattern_overlap_wildcard_vs_specific(tmp_path, fix_log_path):
    """*.pdf overlaps with main.pdf: should be rejected."""
    local, git = _setup_tmp_repo(tmp_path)

    config = _base_config(
        generated_files={
            "all": {"pattern": "*.pdf", "publishers": {"destination": {"file": []}}},
            "main": {"pattern": "main.pdf", "publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(local)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    assert find_by_name(result.events, "config_error.loading.config.generated_files.pattern_overlap"), \
        f"Expected pattern_overlap. events={result.events}"


def test_pattern_overlap_same_compile_dir(tmp_path, fix_log_path):
    """Two patterns with same {compile_dir} prefix: should overlap."""
    local, git = _setup_tmp_repo(tmp_path)
    (local / "papers").mkdir()

    config = _base_config(
        compile={"enabled": False, "dir": "papers"},
        generated_files={
            "a": {"pattern": "{compile_dir}/*.pdf", "publishers": {"destination": {"file": []}}},
            "b": {"pattern": "{compile_dir}/*.pdf", "publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(local)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    assert find_by_name(result.events, "config_error.loading.config.generated_files.pattern_overlap"), \
        f"Expected pattern_overlap. events={result.events}"


def test_pattern_no_overlap_different_extensions(tmp_path, fix_log_path):
    """*.pdf and *.csv: different extensions, no overlap, config should pass."""
    local, git = _setup_tmp_repo(tmp_path)

    config = _base_config(
        generated_files={
            "paper": {"pattern": "*.pdf", "publishers": {"destination": {"file": []}}},
            "data": {"pattern": "*.csv", "publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(local)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    assert has_step_ok(result.events, "config.checked"), \
        f"Config should pass (no overlap). events={result.events}"


# ---------------------------------------------------------------------------
# Pipeline-level tests: wildcard paths (real repo)
# ---------------------------------------------------------------------------

def test_pattern_wildcard_in_directory_same_name_collision(pattern_env, fix_log_path):
    """Wildcards matching files with same name in different dirs: ZP flattens to
    output_dir/filename, so duplicate names collide and persist fails."""
    repo_dir, git, gh, archive_dir, _ = pattern_env

    # Create two files with the same name in different subdirectories
    for sub in ("sub1", "sub2"):
        d = repo_dir / "papers" / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "doc.txt").write_text(f"content from {sub}")

    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    gitignore.write_text(existing + "\npapers/\n")
    git.add_and_commit("add wildcard dirs same name")
    git.push("origin", "main")

    config = _base_config(
        archive_dir,
        generated_files={
            "docs": {
                "pattern": "*ape*/*/*.txt",
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    # ZP detects same-name collision at archive time (flat copy to output_dir)
    # and raises a PipelineError before persist.
    errors = find_errors(result.events)
    assert errors, "Expected error from same-name file collision"
    assert any("collision" in e.get("name", "") for e in errors), \
        f"Expected collision error. Got: {errors}"


def test_pattern_wildcard_in_directory(pattern_env, fix_log_path):
    """Wildcards in directory segments: *ape*/*/*.txt should match nested files
    (unique filenames to avoid ZP flat-copy collision)."""
    repo_dir, git, gh, archive_dir, _ = pattern_env

    # Create directory structure with unique filenames
    for sub in ("sub1", "sub2"):
        d = repo_dir / "papers" / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / f"doc_{sub}.txt").write_text(f"content from {sub}")
    # Also create a non-matching dir
    other = repo_dir / "code" / "sub1"
    other.mkdir(parents=True, exist_ok=True)
    (other / "doc_code.txt").write_text("should not match")

    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    gitignore.write_text(existing + "\npapers/\ncode/\n")
    git.add_and_commit("add wildcard dirs")
    git.push("origin", "main")

    config = _base_config(
        archive_dir,
        generated_files={
            "docs": {
                "pattern": "*ape*/*/*.txt",
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"
    assert find_by_name(result.events, "files.resolved")

    # Should have matched 2 files (papers/sub1/doc_sub1.txt and papers/sub2/doc_sub2.txt)
    persist_dir = archive_dir / TAG
    txt_files = [f for f in fs.list_files(persist_dir) if f.suffix == ".txt"]
    assert len(txt_files) == 2, \
        f"Expected 2 txt files from *ape*/*/*.txt. Got: {[f.name for f in fs.list_files(persist_dir)]}"


def test_pattern_leading_slash_is_project_root(pattern_env, fix_log_path):
    """Pattern starting with / should be relative to project root, not filesystem root."""
    repo_dir, git, gh, archive_dir, _ = pattern_env
    _create_pattern_files(repo_dir, git, {"rootfile.txt": "from project root"})

    config = _base_config(
        archive_dir,
        generated_files={
            "root": {
                "pattern": "/rootfile.txt",
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Leading / should match from project root. errors={errors}"
    assert find_by_name(result.events, "files.resolved")

    persist_dir = archive_dir / TAG
    persisted = persist_dir / "rootfile.txt"
    assert persisted.exists()
    assert persisted.read_text() == "from project root"


def test_pattern_double_star_recursive(pattern_env, fix_log_path):
    """** pattern for recursive matching: should find files at any depth."""
    repo_dir, git, gh, archive_dir, _ = pattern_env

    # Create files at different depths
    (repo_dir / "a.log").write_text("root")
    d1 = repo_dir / "sub"
    d1.mkdir(exist_ok=True)
    (d1 / "b.log").write_text("sub")
    d2 = d1 / "deep"
    d2.mkdir(exist_ok=True)
    (d2 / "c.log").write_text("deep")

    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    gitignore.write_text(existing + "\n*.log\nsub/\n")
    git.add_and_commit("add log files")
    git.push("origin", "main")

    config = _base_config(
        archive_dir,
        generated_files={
            "logs": {
                "pattern": "**/*.log",
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    log_files = [f for f in fs.list_files(persist_dir) if f.suffix == ".log"]
    assert len(log_files) >= 3, \
        f"Expected at least 3 .log files with **/*.log. Got: {[f.name for f in fs.list_files(persist_dir)]}"


# ---------------------------------------------------------------------------
# Pipeline-level tests: pattern matching (real repo)
# ---------------------------------------------------------------------------

def test_pattern_matches_file(pattern_env, fix_log_path):
    """Pattern matching a single file: should resolve and archive it."""
    repo_dir, git, gh, archive_dir, _ = pattern_env
    _create_pattern_files(repo_dir, git, {"output.txt": "test content"})

    config = _base_config(
        archive_dir,
        generated_files={
            "paper": {"pattern": "output.txt", "publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"
    assert find_by_name(result.events, "files.resolved")

    persist_dir = archive_dir / TAG
    assert (persist_dir / "output.txt").exists()


def test_pattern_nonexistent_compile_dir_rejected(pattern_env, fix_log_path):
    """compile.dir set to nonexistent directory: ZP rejects even if compile.enabled=False."""
    repo_dir, git, gh, archive_dir, _ = pattern_env

    config = _base_config(
        archive_dir,
        compile={"enabled": False, "dir": "nonexistent_dir"},
        generated_files={
            "data": {"pattern": "data/results.csv", "publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    assert find_by_name(result.events, "config_error.loading.config.release.compile_dir.not_found"), \
        f"Expected compile_dir.not_found. events={result.events}"


def test_pattern_without_compile_dir_resolves_from_root(pattern_env, fix_log_path):
    """Pattern without {compile_dir}: matches from project root, not compile dir."""
    repo_dir, git, gh, archive_dir, _ = pattern_env
    _create_pattern_files(repo_dir, git, {"data/results.csv": "csv data"})

    # Use an existing directory for compile.dir to avoid config validation error
    existing_dir = repo_dir / "somedir"
    existing_dir.mkdir(exist_ok=True)

    config = _base_config(
        archive_dir,
        compile={"enabled": False, "dir": "somedir"},
        generated_files={
            "data": {"pattern": "data/results.csv", "publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Pattern should match from project root. errors={errors}"
    assert find_by_name(result.events, "files.resolved")


def test_pattern_uses_project_root_not_compile_dir(pattern_env, fix_log_path):
    """Same filename in project root and compile dir: pattern without {compile_dir}
    should archive the one from project root, not compile dir."""
    repo_dir, git, gh, archive_dir, _ = pattern_env

    # Create same file in both locations with different content
    compile_dir = repo_dir / "builddir"
    compile_dir.mkdir(exist_ok=True)
    (compile_dir / "result.txt").write_text("FROM_COMPILE_DIR")
    (repo_dir / "result.txt").write_text("FROM_PROJECT_ROOT")

    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    if "result.txt" not in existing:
        gitignore.write_text(existing + "\nresult.txt\nbuilddir/\n")
    git.add_and_commit("add result files")
    git.push("origin", "main")

    config = _base_config(
        archive_dir,
        compile={"enabled": False, "dir": "builddir"},
        generated_files={
            # No {compile_dir} template: should match from project root
            "result": {"pattern": "result.txt", "publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    # Verify the persisted file is from project root, not compile dir
    persist_dir = archive_dir / TAG
    persisted = persist_dir / "result.txt"
    assert persisted.exists()
    assert persisted.read_text() == "FROM_PROJECT_ROOT", \
        f"Expected content from project root, got: {persisted.read_text()}"


def test_pattern_with_compile_dir_template(pattern_env, fix_log_path):
    """Pattern with {compile_dir}: matches inside compile directory."""
    repo_dir, git, gh, archive_dir, _ = pattern_env

    compile_dir = repo_dir / "build_test"
    compile_dir.mkdir(exist_ok=True)
    _create_pattern_files(repo_dir, git, {"build_test/output.txt": "compiled"})

    config = _base_config(
        archive_dir,
        compile={"enabled": False, "dir": "build_test"},
        generated_files={
            "paper": {"pattern": "{compile_dir}/output.txt", "publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Pattern with compile_dir should work. errors={errors}"
    assert find_by_name(result.events, "files.resolved")


def test_pattern_no_match(pattern_env, fix_log_path):
    """Pattern matching no files: should error pipeline.no_match."""
    repo_dir, git, gh, archive_dir, _ = pattern_env

    config = _base_config(
        archive_dir,
        generated_files={
            "paper": {"pattern": "nonexistent_file_xyz.pdf", "publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert errors, f"Expected error for no match. events={result.events}"
    assert find_by_name(result.events, "pipeline.no_match"), \
        f"Expected pipeline.no_match. Got: {errors}"


def test_pattern_multiple_matches_in_manifest(pattern_env, fix_log_path):
    """Glob matching multiple files: each in manifest with correct hashes."""
    repo_dir, git, gh, archive_dir, _ = pattern_env
    _create_pattern_files(repo_dir, git, {
        "paper1.txt": "content of paper1",
        "paper2.txt": "content of paper2",
        "paper3.txt": "content of paper3",
    })

    config = _base_config(
        archive_dir,
        hash_algorithms=["md5", "sha256"],
        generated_files={
            "papers": {"pattern": "paper*.txt", "publishers": {"destination": {"file": []}}},
            "manifest": {
                "content": {"papers": ["file"]},
                "commit_info": ["sha"],
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    txt_files = sorted(f for f in files if f.suffix == ".txt")
    assert len(txt_files) == 3, \
        f"Expected 3 txt files. Got: {[f.name for f in files]}"

    # Manifest should have 3 entries
    manifest_files = [f for f in files if "manifest" in f.name and f.suffix == ".json"]
    assert manifest_files
    manifest = fs.parse_manifest(manifest_files[0])
    assert len(manifest["files"]) == 3, \
        f"Expected 3 entries in manifest. Got: {manifest['files']}"

    # Verify hashes match locally computed values
    file_hashes = find_data(result.events, "file_hashes")
    assert file_hashes

    for txt in txt_files:
        assert txt.name in file_hashes, \
            f"{txt.name} missing from file_hashes"
        for algo in ("md5", "sha256"):
            local_hash = fs.compute_hash(txt, algo)
            assert file_hashes[txt.name][algo] == local_hash, \
                f"{txt.name} {algo} mismatch: reported={file_hashes[txt.name][algo]}, local={local_hash}"


def test_pattern_multiple_matches_all_signed(pattern_env, fix_log_path):
    """Glob with sign=true: each matched file gets its own signature, verified with gpg."""
    repo_dir, git, gh, archive_dir, gpg_uid = pattern_env
    _create_pattern_files(repo_dir, git, {
        "doc1.txt": "content 1",
        "doc2.txt": "content 2",
        "doc3.txt": "content 3",
    })

    config = _base_config(
        archive_dir,
        signing={"sign": True, "sign_mode": "file", "gpg": {"uid": gpg_uid}},
        generated_files={
            "docs": {
                "pattern": "doc*.txt",
                "sign": True,
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    txt_files = sorted(f for f in files if f.suffix == ".txt")
    sig_files = sorted(f for f in files if f.name.endswith(".asc"))

    assert len(txt_files) == 3, f"Expected 3 txt files. Got: {[f.name for f in files]}"
    assert len(sig_files) == 3, f"Expected 3 signatures. Got: {[f.name for f in files]}"

    # Each signature verifies its own file
    import subprocess as sp
    for txt in txt_files:
        sig = persist_dir / "gpg_sign" / f"{txt.name}.asc"
        assert sig.exists(), f"Missing {sig.name}"
        verify = sp.run(["gpg", "--verify", str(sig), str(txt)],
                        capture_output=True, text=True)
        assert verify.returncode == 0, \
            f"GPG verify failed for {txt.name}: {verify.stderr}"

    # Verify hashes match
    file_hashes = find_data(result.events, "file_hashes")
    for txt in txt_files:
        local_hash = fs.compute_hash(txt, "sha256")
        assert file_hashes[txt.name]["sha256"] == local_hash, \
            f"{txt.name} sha256 mismatch"
