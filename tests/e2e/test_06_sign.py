"""Test: signing, manifest, and GitHub asset publishing.

Uses the real test repo (repo_env). ZP creates the tag + release itself.
Tests verify signing modes, manifest generation, and GitHub asset upload
by inspecting persisted files and querying GitHub via GithubClient.
"""

import json
import tempfile
from pathlib import Path

import pytest

from tests.utils.cli import ZpRunner
from tests.utils.github import GithubClient
from tests.utils.ndjson import (
    find_by_name, find_data, find_errors,
)
from tests.utils import fs


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TAG = "v-test-sign"
TAG_ANNOTATED = "v-test-sign-annotated"


def _signing_on(gpg_uid, **overrides):
    """Build a signing config with the test GPG UID."""
    cfg = {"sign": True, "gpg": {"uid": gpg_uid}}
    cfg.update(overrides)
    return cfg


def _base_config(archive_dir: Path, **overrides) -> dict:
    config = {
        "project_name": {"prefix": "TestProject", "suffix": "-{tag_name}"},
        "main_branch": "main",
        "compile": {"enabled": False},
        "hash_algorithms": ["sha256"],
        "archive": {"format": "zip", "dir": str(archive_dir)},
        "prompt_validation_level": "danger",
    }
    config.update(overrides)
    return config


# Prompts: ZP creates the release itself.
_PROMPTS = {
    "enter_tag": TAG,
    "release_title": "",
    "release_notes": "",
    "confirm_build": "yes",
    "confirm_publish": "yes",
    "confirm_gpg_key": "yes",
}

_TEST_CONFIG = {"prompts": _PROMPTS, "verify_prompts": False}

# Same but skip publish confirmation
_TEST_CONFIG_NO_PUBLISH = {
    "prompts": {**_PROMPTS, "confirm_publish": "no"},
    "verify_prompts": False,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sign_env(repo_env, fix_gpg_uid):
    """Yield (repo_dir, git, gh, archive_dir, gpg_uid). Cleanup release after test."""
    repo_dir, git = repo_env
    gh = GithubClient(repo_dir)

    # Cleanup leftover from a previous failed run
    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)
    git._run("tag", "-d", TAG, check=False)
    git._run("push", "origin", f":refs/tags/{TAG}", check=False)

    archive_dir = Path(tempfile.mkdtemp())

    yield repo_dir, git, gh, archive_dir, fix_gpg_uid

    # Cleanup
    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)


def _create_pattern_file(repo_dir, git):
    """Create a pattern file (output.txt) gitignored so repo stays clean."""
    (repo_dir / "output.txt").write_text("compiled output for signing")
    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    if "output.txt" not in existing:
        gitignore.write_text(existing + "\noutput.txt\n")
        git.add_and_commit("gitignore output.txt")
        git.push()


# ---------------------------------------------------------------------------
# Tests: signing on/off
# ---------------------------------------------------------------------------

def test_sign_project_file_mode(sign_env, fix_log_path):
    """Sign project archive in FILE mode: .asc signature should exist."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    config = _base_config(
        archive_dir,
        signing=_signing_on(gpg_uid, sign_mode="file"),
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    names = [f.name for f in files]

    zip_files = [f for f in files if f.suffix == ".zip"]
    assert zip_files, f"Expected .zip. Got: {names}"

    sig_files = [f for f in files if f.name.endswith(".asc")]
    assert sig_files, f"Expected .asc signature. Got: {names}"

    # Signature should reference the archive
    assert any(zip_files[0].name in s.name for s in sig_files), \
        f"Signature should reference archive. zip={zip_files[0].name}, sigs={[s.name for s in sig_files]}"


def test_sign_project_file_hash_mode(sign_env, fix_log_path):
    """Sign project archive in FILE_HASH mode: .asc signature of hash should exist."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    config = _base_config(
        archive_dir,
        identity_hash_algo="sha256",
        signing=_signing_on(gpg_uid, sign_mode="file_hash"),
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    names = [f.name for f in files]

    sig_files = [f for f in files if f.name.endswith(".asc")]
    assert sig_files, f"Expected .asc signature. Got: {names}"


def test_sign_pattern_only(sign_env, fix_log_path):
    """Sign a pattern file only (no project): signature should exist."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    _create_pattern_file(repo_dir, git)

    config = _base_config(
        archive_dir,
        signing=_signing_on(gpg_uid, sign_mode="file"),
        generated_files={
            "paper": {
                "pattern": "output.txt",
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    names = [f.name for f in files]

    assert "output.txt" in names
    assert "output.txt.asc" in names, f"Expected output.txt.asc. Got: {names}"


def test_sign_both_project_and_pattern(sign_env, fix_log_path):
    """Sign both project and pattern: two signatures should exist."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    _create_pattern_file(repo_dir, git)

    config = _base_config(
        archive_dir,
        signing=_signing_on(gpg_uid, sign_mode="file"),
        generated_files={
            "paper": {
                "pattern": "output.txt",
                "publishers": {"destination": {"file": []}},
            },
            "project": {"publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    sig_files = [f for f in files if f.name.endswith(".asc")]
    assert len(sig_files) == 2, \
        f"Expected exactly 2 signatures. Got: {[f.name for f in files]}"


def test_no_sign(sign_env, fix_log_path):
    """signing.sign: false — no .asc files should be created."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    config = _base_config(
        archive_dir,
        signing={"sign": False},
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    sig_files = [f for f in files if f.name.endswith(".asc") or f.name.endswith(".sig")]
    assert not sig_files, f"No signatures expected. Got: {[f.name for f in files]}"


def test_sign_per_file_override(sign_env, fix_log_path):
    """Per-file sign override: project signed, pattern not."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    _create_pattern_file(repo_dir, git)

    config = _base_config(
        archive_dir,
        signing={"sign": False, "gpg": {"uid": gpg_uid}},  # global off, but uid set for per-file
        generated_files={
            "paper": {
                "pattern": "output.txt",
                "sign": False,
                "publishers": {"destination": {"file": []}},
            },
            "project": {
                "sign": True,  # per-file override
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    names = [f.name for f in files]

    # Project should have a signature
    zip_files = [f for f in files if f.suffix == ".zip"]
    assert zip_files
    assert any(zip_files[0].name in n for n in names if n.endswith(".asc")), \
        f"Expected signature for project. Got: {names}"

    # Pattern should NOT have a signature
    assert "output.txt.asc" not in names, \
        f"Pattern should not be signed. Got: {names}"


# ---------------------------------------------------------------------------
# Tests: signing with different hash algos
# ---------------------------------------------------------------------------

def test_sign_binary_sig_format(sign_env, fix_log_path):
    """Without --armor: should produce binary .sig instead of .asc."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    config = _base_config(
        archive_dir,
        signing={
            "sign": True,
            "sign_mode": "file",
            "gpg": {"uid": gpg_uid, "extra_args": ["--no-armor"]},  # removes default --armor
        },
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    names = [f.name for f in files]

    sig_files = [f for f in files if f.name.endswith(".sig")]
    assert sig_files, f"Expected .sig (binary) signature. Got: {names}"
    # No .asc should exist
    asc_files = [f for f in files if f.name.endswith(".asc")]
    assert not asc_files, f"Should not have .asc with binary mode. Got: {names}"


def test_sign_invalid_gpg_uid(sign_env, fix_log_path):
    """Invalid GPG UID: signing should fail with an error."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    config = _base_config(
        archive_dir,
        signing={
            "sign": True,
            "sign_mode": "file",
            "gpg": {"uid": "nonexistent-key@fake-domain.invalid"},
        },
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert errors, f"Expected GPG error with invalid UID. events={result.events}"
    assert find_by_name(result.events, "gpg.no_secret_key"), \
        f"Expected gpg.no_secret_key. Got: {errors}"



def test_sign_without_uid_uses_default(sign_env, fix_log_path):
    """Sign without gpg.uid in config: ZP should use the default GPG key."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    config = _base_config(
        archive_dir,
        signing={"sign": True, "sign_mode": "file"},  # no gpg.uid
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors without UID (default key): {errors}"

    persist_dir = archive_dir / TAG
    sig_files = [f for f in fs.list_files(persist_dir) if f.name.endswith(".asc")]
    assert sig_files, f"Expected .asc signature with default key"


@pytest.mark.parametrize("sign_hash_algo", ["md5", "sha1", "sha256", "sha512"])
def test_sign_file_hash_algo(sign_env, fix_log_path, sign_hash_algo):
    """FILE_HASH mode with various hash algos: signature should exist."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    config = _base_config(
        archive_dir,
        identity_hash_algo=sign_hash_algo,
        signing=_signing_on(gpg_uid, sign_mode="file_hash"),
        hash_algorithms=["sha256", sign_hash_algo],
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors with {sign_hash_algo}: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    sig_files = [f for f in files if f.name.endswith(".asc")]
    assert sig_files, \
        f"Expected .asc signature with {sign_hash_algo}. Got: {[f.name for f in files]}"


# ---------------------------------------------------------------------------
# Tests: manifest
# ---------------------------------------------------------------------------

def test_manifest_project_only_verify_hashes(sign_env, fix_log_path):
    """Manifest with project only: verify file_hashes event matches locally computed values."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    config = _base_config(
        archive_dir,
        signing={"sign": False},
        hash_algorithms=["md5", "sha256"],
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
            "manifest": {
                "files": ["project"],
                "commit_info": ["sha", "date_epoch"],
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG

    # Manifest structure
    manifest_files = [f for f in fs.list_files(persist_dir)
                      if "manifest" in f.name and f.suffix == ".json"]
    assert manifest_files
    manifest = fs.parse_manifest(manifest_files[0])
    assert "version" in manifest
    assert "commit" in manifest
    assert "files" in manifest
    assert len(manifest["files"]) == 1

    # File entry key should reference a real file on disk
    entry_key = manifest["files"][0]["key"]
    persisted_names = [f.name for f in fs.list_files(persist_dir)]
    assert entry_key in persisted_names, \
        f"Manifest entry key '{entry_key}' not found on disk. Got: {persisted_names}"

    # Verify file_hashes data event against locally computed hashes
    file_hashes = find_data(result.events, "file_hashes")
    assert file_hashes, f"No file_hashes data event. events={result.events}"

    for filename, reported_hashes in file_hashes.items():
        persisted = persist_dir / filename
        if not persisted.exists():
            continue
        for algo, reported_value in reported_hashes.items():
            if algo in ("tree", "tree256"):
                continue  # tree hashes need extraction, tested in test_04
            local_hash = fs.compute_hash(persisted, algo)
            assert reported_value == local_hash, \
                f"{filename} {algo} mismatch: zp={reported_value}, local={local_hash}"


def test_manifest_verify_commit_sha(sign_env, fix_log_path):
    """Manifest commit.sha should match git rev-parse HEAD on the repo."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    config = _base_config(
        archive_dir,
        signing={"sign": False},
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
            "manifest": {
                "files": ["project"],
                "commit_info": ["sha", "date_epoch"],
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    manifest_files = [f for f in fs.list_files(persist_dir)
                      if "manifest" in f.name and f.suffix == ".json"]
    assert manifest_files

    manifest = fs.parse_manifest(manifest_files[0])
    local_sha = git.rev_parse("HEAD")
    assert manifest["commit"]["sha"] == local_sha, \
        f"SHA mismatch: manifest={manifest['commit']['sha']}, local={local_sha}"

    # Verify date_epoch matches git log
    r = git._run("log", "-1", "--format=%ct", "HEAD")
    local_epoch = r.stdout.strip()
    assert str(manifest["commit"]["date_epoch"]) == local_epoch, \
        f"Epoch mismatch: manifest={manifest['commit']['date_epoch']}, local={local_epoch}"


def test_manifest_with_pattern_and_project(sign_env, fix_log_path):
    """Manifest referencing both: verify each file entry has correct hashes."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    _create_pattern_file(repo_dir, git)

    config = _base_config(
        archive_dir,
        signing={"sign": False},
        hash_algorithms=["md5", "sha256"],
        generated_files={
            "paper": {
                "pattern": "output.txt",
                "publishers": {"destination": {"file": []}},
            },
            "project": {"publishers": {"destination": {"file": []}}},
            "manifest": {
                "files": ["paper", "project"],
                "commit_info": ["sha"],
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    manifest_files = [f for f in fs.list_files(persist_dir)
                      if "manifest" in f.name and f.suffix == ".json"]
    assert manifest_files

    manifest = fs.parse_manifest(manifest_files[0])
    assert len(manifest["files"]) == 2, \
        f"Expected 2 files in manifest. Got: {manifest['files']}"

    # Verify each manifest entry hash matches the real file
    for entry in manifest["files"]:
        filename = entry.get("key", entry.get("filename", ""))
        # Find the corresponding persisted file
        candidates = [f for f in fs.list_files(persist_dir)
                      if f.name == filename and f.suffix != ".json"]
        if not candidates:
            # Try matching by stem (e.g. output.txt)
            candidates = [f for f in fs.list_files(persist_dir)
                          if filename in f.name and not f.name.endswith(".json")]
        assert candidates, f"No persisted file for manifest entry '{filename}'"

        for algo in ("md5", "sha256"):
            if algo in entry:
                local_hash = fs.compute_hash(candidates[0], algo)
                assert entry[algo] == local_hash, \
                    f"{filename} {algo} mismatch: manifest={entry[algo]}, local={local_hash}"


def test_manifest_project_only_no_pattern(sign_env, fix_log_path):
    """Manifest with only project (no pattern): should have exactly 1 file entry."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    config = _base_config(
        archive_dir,
        signing={"sign": False},
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
            "manifest": {
                "files": ["project"],
                "commit_info": ["sha"],
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    manifest_files = [f for f in fs.list_files(persist_dir)
                      if "manifest" in f.name and f.suffix == ".json"]
    manifest = fs.parse_manifest(manifest_files[0])
    assert len(manifest["files"]) == 1, \
        f"Expected exactly 1 file. Got: {manifest['files']}"


def test_manifest_signed(sign_env, fix_log_path):
    """Manifest itself can be signed: .asc should exist for manifest."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    config = _base_config(
        archive_dir,
        signing=_signing_on(gpg_uid, sign_mode="file"),
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
            "manifest": {
                "files": ["project"],
                "sign": True,
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    names = [f.name for f in files]

    manifest_files = [f for f in files if "manifest" in f.name and f.suffix == ".json"]
    assert manifest_files
    manifest_sigs = [n for n in names if "manifest" in n and n.endswith(".asc")]
    assert manifest_sigs, f"Expected manifest .asc. Got: {names}"


def test_manifest_commit_info_all_fields(sign_env, fix_log_path):
    """All commit_info fields: verify values are non-empty and sha matches git."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    config = _base_config(
        archive_dir,
        signing={"sign": False},
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
            "manifest": {
                "files": ["project"],
                "commit_info": ["sha", "date_epoch", "subject", "author_name", "author_email"],
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    manifest_files = [f for f in fs.list_files(persist_dir)
                      if "manifest" in f.name and f.suffix == ".json"]
    manifest = fs.parse_manifest(manifest_files[0])
    commit = manifest["commit"]

    for field in ("sha", "date_epoch", "subject", "author_name", "author_email"):
        assert field in commit, f"Missing '{field}' in commit. Got: {list(commit.keys())}"
        assert commit[field], f"'{field}' should not be empty"

    # sha should match local
    local_sha = git.rev_parse("HEAD")
    assert commit["sha"] == local_sha, \
        f"SHA mismatch: manifest={commit['sha']}, local={local_sha}"


def test_manifest_minimal_commit_info(sign_env, fix_log_path):
    """Manifest with only sha in commit_info: other fields should be absent."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    config = _base_config(
        archive_dir,
        signing={"sign": False},
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
            "manifest": {
                "files": ["project"],
                "commit_info": ["sha"],
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    manifest_files = [f for f in fs.list_files(persist_dir)
                      if "manifest" in f.name and f.suffix == ".json"]
    manifest = fs.parse_manifest(manifest_files[0])
    commit = manifest["commit"]

    assert "sha" in commit
    # Only requested field should be present
    assert "date_epoch" not in commit, \
        f"date_epoch should not be in commit when not requested. Got: {commit}"


# ---------------------------------------------------------------------------
# Tests: manifest with lightweight vs annotated tags
# ---------------------------------------------------------------------------

def test_manifest_lightweight_tag(sign_env, fix_log_path):
    """Lightweight tag (created by ZP): version.sha == commit.sha (same ref)."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    config = _base_config(
        archive_dir,
        signing={"sign": False},
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
            "manifest": {
                "files": ["project"],
                "commit_info": ["sha", "tag_sha"],
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG_NO_PUBLISH,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    manifest_files = [f for f in fs.list_files(persist_dir)
                      if "manifest" in f.name and f.suffix == ".json"]
    manifest = fs.parse_manifest(manifest_files[0])

    commit_sha = manifest["commit"]["sha"]
    assert "sha" in manifest["version"], \
        f"version.sha missing from manifest. version={manifest['version']}"
    version_sha = manifest["version"]["sha"]

    # For a lightweight tag, tag SHA == commit SHA
    assert version_sha == commit_sha, \
        f"Lightweight tag: version.sha should equal commit.sha. " \
        f"version.sha={version_sha}, commit.sha={commit_sha}"

    # Both should match local HEAD
    local_sha = git.rev_parse("HEAD")
    assert commit_sha == local_sha


def test_manifest_annotated_tag(repo_env, fix_log_path):
    """Annotated tag: version.sha (tag object) != commit.sha."""
    repo_dir, git = repo_env
    gh = GithubClient(repo_dir)

    # Cleanup leftover
    if gh.has_release(TAG_ANNOTATED):
        gh.delete_release(TAG_ANNOTATED, cleanup_tag=True)
    git._run("tag", "-d", TAG_ANNOTATED, check=False)
    git._run("push", "origin", f":refs/tags/{TAG_ANNOTATED}", check=False)

    # Create annotated tag + push BEFORE ZP runs
    git.tag_create(TAG_ANNOTATED, annotated=True, msg="Annotated release")
    git._run("push", "origin", TAG_ANNOTATED)
    gh.create_release(TAG_ANNOTATED, title=TAG_ANNOTATED, body="annotated test")

    archive_dir = Path(tempfile.mkdtemp())
    prompts_annotated = {
        # Release already exists → no enter_tag/title/notes prompts
        "confirm_build": "yes",
        "confirm_publish": "no",
    }

    try:
        config = _base_config(
            archive_dir,
            signing={"sign": False},
            generated_files={
                "project": {"publishers": {"destination": {"file": []}}},
                "manifest": {
                    "files": ["project"],
                    "commit_info": ["sha", "tag_sha"],
                    "publishers": {"destination": {"file": []}},
                },
            },
        )

        runner = ZpRunner(repo_dir)
        result = runner.run_test("release", config=config,
                                 test_config={"prompts": prompts_annotated, "verify_prompts": False},
                                 log_path=fix_log_path,
                                 fail_on="ignore")

        errors = find_errors(result.events)
        assert not errors, f"Unexpected errors: {errors}"

        persist_dir = archive_dir / TAG_ANNOTATED
        manifest_files = [f for f in fs.list_files(persist_dir)
                          if "manifest" in f.name and f.suffix == ".json"]
        manifest = fs.parse_manifest(manifest_files[0])

        commit_sha = manifest["commit"]["sha"]
        assert "sha" in manifest["version"], \
            f"version.sha missing from manifest. version={manifest['version']}"
        version_sha = manifest["version"]["sha"]

        # For an annotated tag, tag object SHA != commit SHA
        assert version_sha != commit_sha, \
            f"Annotated tag: version.sha should differ from commit.sha. " \
            f"version.sha={version_sha}, commit.sha={commit_sha}"

        # commit.sha should match HEAD
        local_commit = git.rev_parse("HEAD")
        assert commit_sha == local_commit

        # version.sha should match the tag object (not dereferenced)
        local_tag_sha = git.rev_parse(TAG_ANNOTATED)
        assert version_sha == local_tag_sha, \
            f"version.sha should be tag object SHA. " \
            f"version.sha={version_sha}, local tag SHA={local_tag_sha}"

    finally:
        if gh.has_release(TAG_ANNOTATED):
            gh.delete_release(TAG_ANNOTATED, cleanup_tag=True)


# ---------------------------------------------------------------------------
# Tests: GitHub asset publishing
# ---------------------------------------------------------------------------

def test_publish_pattern_as_github_asset(sign_env, fix_log_path):
    """Pattern file published to GitHub: verify asset exists via GithubClient."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    _create_pattern_file(repo_dir, git)

    config = _base_config(
        archive_dir,
        signing={"sign": False},
        generated_files={
            "paper": {
                "pattern": "output.txt",
                "publishers": {"destination": {"file": ["github"]}},
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

    # Verify asset exists on GitHub
    asset = gh.get_release_asset(TAG, "output.txt")
    assert asset is not None, \
        f"output.txt should be a release asset. Assets: {gh.list_release_assets(TAG)}"

    # Download and verify hash
    with tempfile.TemporaryDirectory() as tmp:
        downloaded = gh.download_asset(TAG, "output.txt", Path(tmp))
        local_hash = fs.compute_hash(downloaded, "sha256")

    persist_dir = archive_dir / TAG
    persisted = persist_dir / "output.txt"
    assert persisted.exists()
    expected_hash = fs.compute_hash(persisted, "sha256")
    assert local_hash == expected_hash, \
        f"Downloaded asset hash mismatch: {local_hash} != {expected_hash}"


def test_publish_manifest_as_github_asset(sign_env, fix_log_path):
    """Manifest published to GitHub: verify asset exists and content is valid JSON."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env

    config = _base_config(
        archive_dir,
        signing={"sign": False},
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
            "manifest": {
                "files": ["project"],
                "commit_info": ["sha"],
                "publishers": {"destination": {"file": ["github"]}},
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

    # Find the manifest filename from persisted files
    persist_dir = archive_dir / TAG
    manifest_files = [f for f in fs.list_files(persist_dir)
                      if "manifest" in f.name and f.suffix == ".json"]
    assert manifest_files

    manifest_name = manifest_files[0].name

    # Verify asset exists on GitHub
    asset = gh.get_release_asset(TAG, manifest_name)
    assert asset is not None, \
        f"{manifest_name} should be a release asset. Assets: {gh.list_release_assets(TAG)}"

    # Download and verify it's valid JSON with correct hash
    with tempfile.TemporaryDirectory() as tmp:
        downloaded = gh.download_asset(TAG, manifest_name, Path(tmp))
        content = downloaded.read_text()
        parsed = json.loads(content)
        assert "files" in parsed
        assert "commit" in parsed

        dl_hash = fs.compute_hash(downloaded, "sha256")

    local_hash = fs.compute_hash(manifest_files[0], "sha256")
    assert dl_hash == local_hash, \
        f"Downloaded manifest hash mismatch: {dl_hash} != {local_hash}"


def test_publish_signed_pattern_and_sig_as_github_assets(sign_env, fix_log_path):
    """Signed pattern + signature both uploaded to GitHub."""
    repo_dir, git, gh, archive_dir, gpg_uid = sign_env
    _create_pattern_file(repo_dir, git)

    config = _base_config(
        archive_dir,
        signing=_signing_on(gpg_uid, sign_mode="file"),
        generated_files={
            "paper": {
                "pattern": "output.txt",
                "publishers": {
                    "destination": {"file": ["github"], "sig": ["github"]},
                },
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

    # Both file and signature should be assets
    assets = gh.list_release_assets(TAG)
    asset_names = [a["name"] for a in assets]

    assert "output.txt" in asset_names, \
        f"output.txt should be an asset. Got: {asset_names}"
    assert "output.txt.asc" in asset_names, \
        f"output.txt.asc should be an asset. Got: {asset_names}"

    # Download both and verify hashes
    persist_dir = archive_dir / TAG
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("output.txt", "output.txt.asc"):
            downloaded = gh.download_asset(TAG, name, Path(tmp))
            dl_hash = fs.compute_hash(downloaded, "sha256")
            local_hash = fs.compute_hash(persist_dir / name, "sha256")
            assert dl_hash == local_hash, \
                f"{name} hash mismatch: downloaded={dl_hash}, local={local_hash}"
