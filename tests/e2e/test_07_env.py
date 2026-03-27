"""Test: environment variables passed to make during compilation.

Verifies that ZP passes correct ZP_* env vars to the Makefile,
that SOURCE_DATE_EPOCH is derived from the commit (not wall clock),
and that different commits produce different epochs.
"""

import tempfile
from pathlib import Path

import pytest

from tests.utils.cli import ZpRunner
from tests.utils.github import GithubClient
from tests.utils.ndjson import find_by_name, find_errors
from tests.utils import fs


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TAG = "v-test-env"

# Makefile that captures all ZP_* env vars into a file for inspection
MAKEFILE = """\
.PHONY: deploy
deploy:
\t@echo "ZP_COMMIT_DATE_EPOCH=$${ZP_COMMIT_DATE_EPOCH}" > env_dump.txt
\t@echo "ZP_COMMIT_SHA=$${ZP_COMMIT_SHA}" >> env_dump.txt
\t@echo "ZP_COMMIT_SUBJECT=$${ZP_COMMIT_SUBJECT}" >> env_dump.txt
\t@echo "ZP_COMMIT_AUTHOR_NAME=$${ZP_COMMIT_AUTHOR_NAME}" >> env_dump.txt
\t@echo "ZP_COMMIT_AUTHOR_EMAIL=$${ZP_COMMIT_AUTHOR_EMAIL}" >> env_dump.txt
\t@echo "ZP_COMMIT_COMMITTER_NAME=$${ZP_COMMIT_COMMITTER_NAME}" >> env_dump.txt
\t@echo "ZP_COMMIT_COMMITTER_EMAIL=$${ZP_COMMIT_COMMITTER_EMAIL}" >> env_dump.txt
\t@echo "ZP_BRANCH=$${ZP_BRANCH}" >> env_dump.txt
\t@echo "ZP_ORIGIN_URL=$${ZP_ORIGIN_URL}" >> env_dump.txt
\t@echo "SOURCE_DATE_EPOCH=$${ZP_COMMIT_DATE_EPOCH}" >> env_dump.txt
"""

_PROMPTS = {
    "enter_tag": TAG,
    "release_title": "",
    "release_notes": "",
    "confirm_build": "yes",
    "confirm_publish": "no",
    "confirm_persist_overwrite": "yes",
}

_TEST_CONFIG = {"prompts": _PROMPTS, "verify_prompts": False}


def _base_config(archive_dir: Path, compile_dir: str) -> dict:
    return {
        "project_name": {"prefix": "TestProject", "suffix": "-{tag_name}"},
        "main_branch": "main",
        "compile": {"enabled": True, "dir": compile_dir},
        "signing": {"sign": False},
        "hash_algorithms": ["sha256"],
        "archive": {"format": "zip", "dir": str(archive_dir)},
        "prompt_validation_level": "danger",
        "generated_files": {
            "project": {"publishers": {"file_destination": []}},
        },
    }


def _parse_env_dump(path: Path) -> dict[str, str]:
    """Parse the env_dump.txt written by the test Makefile."""
    result = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def env_test(repo_env):
    """Setup Makefile, yield (repo_dir, git, gh, archive_dir). Cleanup after."""
    repo_dir, git = repo_env
    gh = GithubClient(repo_dir)

    # Cleanup leftover
    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)
    git._run("tag", "-d", TAG, check=False)
    git._run("push", "origin", f":refs/tags/{TAG}", check=False)

    # Setup Makefile + gitignore env_dump.txt
    (repo_dir / "Makefile").write_text(MAKEFILE)
    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    if "env_dump.txt" not in existing:
        gitignore.write_text(existing + "\nenv_dump.txt\n")
    git.add_and_commit("add test Makefile")
    git.push()

    archive_dir = Path(tempfile.mkdtemp())

    yield repo_dir, git, gh, archive_dir

    # Cleanup env_dump.txt so repo stays clean for reset
    (repo_dir / "env_dump.txt").unlink(missing_ok=True)
    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)


# ---------------------------------------------------------------------------
# Tests: ZP_* env vars passed to make
# ---------------------------------------------------------------------------

def test_env_vars_passed_to_make(env_test, fix_log_path):
    """All ZP_* env vars should be passed to make and be non-empty."""
    repo_dir, git, gh, archive_dir = env_test
    config = _base_config(archive_dir, compile_dir=str(repo_dir))
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    env_dump = repo_dir / "env_dump.txt"
    assert env_dump.exists(), "Makefile should have written env_dump.txt"

    env = _parse_env_dump(env_dump)

    expected_keys = [
        "ZP_COMMIT_DATE_EPOCH",
        "ZP_COMMIT_SHA",
        "ZP_COMMIT_SUBJECT",
        "ZP_COMMIT_AUTHOR_NAME",
        "ZP_COMMIT_AUTHOR_EMAIL",
        "ZP_COMMIT_COMMITTER_NAME",
        "ZP_COMMIT_COMMITTER_EMAIL",
        "ZP_BRANCH",
        "ZP_ORIGIN_URL",
    ]
    for key in expected_keys:
        assert key in env, f"Missing {key} in env dump. Got: {list(env.keys())}"
        assert env[key], f"{key} should not be empty"


def test_env_sha_matches_git(env_test, fix_log_path):
    """ZP_COMMIT_SHA should match git rev-parse HEAD."""
    repo_dir, git, gh, archive_dir = env_test
    config = _base_config(archive_dir, compile_dir=str(repo_dir))
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    env = _parse_env_dump(repo_dir / "env_dump.txt")
    local_sha = git.rev_parse("HEAD")
    assert env["ZP_COMMIT_SHA"] == local_sha, \
        f"SHA mismatch: env={env['ZP_COMMIT_SHA']}, git={local_sha}"


def test_env_epoch_matches_commit(env_test, fix_log_path):
    """ZP_COMMIT_DATE_EPOCH should match git log commit timestamp, not wall clock."""
    repo_dir, git, gh, archive_dir = env_test
    config = _base_config(archive_dir, compile_dir=str(repo_dir))
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    env = _parse_env_dump(repo_dir / "env_dump.txt")

    # Epoch from git log
    r = git._run("log", "-1", "--format=%ct", "HEAD")
    local_epoch = r.stdout.strip()
    assert env["ZP_COMMIT_DATE_EPOCH"] == local_epoch, \
        f"Epoch mismatch: env={env['ZP_COMMIT_DATE_EPOCH']}, git={local_epoch}"

    # SOURCE_DATE_EPOCH should be the same
    assert env["SOURCE_DATE_EPOCH"] == local_epoch, \
        f"SOURCE_DATE_EPOCH mismatch: env={env['SOURCE_DATE_EPOCH']}, git={local_epoch}"


def test_env_epoch_stable_across_runs(env_test, fix_log_path):
    """Running twice on the same commit: epoch should be identical (not wall clock)."""
    repo_dir, git, gh, archive_dir = env_test
    config = _base_config(archive_dir, compile_dir=str(repo_dir))

    # First run
    runner = ZpRunner(repo_dir)
    result1 = runner.run_test("release", config=config,
                              test_config=_TEST_CONFIG,
                              log_path=fix_log_path,
                              fail_on="ignore")
    errors = find_errors(result1.events)
    assert not errors, f"Run 1 errors: {errors}"
    env1 = _parse_env_dump(repo_dir / "env_dump.txt")

    # Cleanup release for second run
    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)
    git._run("tag", "-d", TAG, check=False)
    git._run("push", "origin", f":refs/tags/{TAG}", check=False)

    # Second run (same commit, different wall clock time)
    runner2 = ZpRunner(repo_dir)
    result2 = runner2.run_test("release", config=config,
                               test_config=_TEST_CONFIG,
                               log_path=fix_log_path,
                               fail_on="ignore")
    errors = find_errors(result2.events)
    assert not errors, f"Run 2 errors: {errors}"
    env2 = _parse_env_dump(repo_dir / "env_dump.txt")

    assert env1["ZP_COMMIT_DATE_EPOCH"] == env2["ZP_COMMIT_DATE_EPOCH"], \
        f"Epoch should be stable: run1={env1['ZP_COMMIT_DATE_EPOCH']}, run2={env2['ZP_COMMIT_DATE_EPOCH']}"
    assert env1["ZP_COMMIT_SHA"] == env2["ZP_COMMIT_SHA"]


def test_env_epoch_changes_with_commit(env_test, fix_log_path):
    """Different commits should produce different ZP_COMMIT_DATE_EPOCH."""
    repo_dir, git, gh, archive_dir = env_test
    config = _base_config(archive_dir, compile_dir=str(repo_dir))

    # First run: current commit
    runner = ZpRunner(repo_dir)
    result1 = runner.run_test("release", config=config,
                              test_config=_TEST_CONFIG,
                              log_path=fix_log_path,
                              fail_on="ignore")
    errors = find_errors(result1.events)
    assert not errors, f"Run 1 errors: {errors}"
    env1 = _parse_env_dump(repo_dir / "env_dump.txt")
    sha1 = env1["ZP_COMMIT_SHA"]

    # Cleanup release
    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)
    git._run("tag", "-d", TAG, check=False)
    git._run("push", "origin", f":refs/tags/{TAG}", check=False)

    # Create a new commit (with a sleep-free timestamp difference via GIT_COMMITTER_DATE)
    git.add_file("epoch_test.txt", "new content for epoch test")
    git._run("add", ".")
    git._run("commit", "-m", "second commit for epoch test",
             "--date", "2030-01-01T00:00:00+00:00")
    git.push()

    # Second run: new commit
    runner2 = ZpRunner(repo_dir)
    result2 = runner2.run_test("release", config=config,
                               test_config=_TEST_CONFIG,
                               log_path=fix_log_path,
                               fail_on="ignore")
    errors = find_errors(result2.events)
    assert not errors, f"Run 2 errors: {errors}"
    env2 = _parse_env_dump(repo_dir / "env_dump.txt")
    sha2 = env2["ZP_COMMIT_SHA"]

    # Different commits
    assert sha1 != sha2, \
        f"Commits should differ: {sha1} vs {sha2}"

    # Different epochs (commit timestamps differ)
    assert env1["ZP_COMMIT_DATE_EPOCH"] != env2["ZP_COMMIT_DATE_EPOCH"], \
        f"Epoch should change with commit: {env1['ZP_COMMIT_DATE_EPOCH']} vs {env2['ZP_COMMIT_DATE_EPOCH']}"

    # Second epoch should match the forced date
    r = git._run("log", "-1", "--format=%ct", "HEAD")
    assert env2["ZP_COMMIT_DATE_EPOCH"] == r.stdout.strip()


def test_env_branch_and_origin(env_test, fix_log_path):
    """ZP_BRANCH and ZP_ORIGIN_URL should match git state."""
    repo_dir, git, gh, archive_dir = env_test
    config = _base_config(archive_dir, compile_dir=str(repo_dir))
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    env = _parse_env_dump(repo_dir / "env_dump.txt")

    assert env["ZP_BRANCH"] == git.branch_current(), \
        f"Branch mismatch: env={env['ZP_BRANCH']}, git={git.branch_current()}"

    assert env["ZP_ORIGIN_URL"] == git.remote_url(), \
        f"Origin mismatch: env={env['ZP_ORIGIN_URL']}, git={git.remote_url()}"


# ---------------------------------------------------------------------------
# Tests: persist overwrite
# ---------------------------------------------------------------------------

def test_persist_overwrite_accepted(env_test, fix_log_path):
    """Second run to same archive_dir: confirm_persist_overwrite=yes should succeed."""
    repo_dir, git, gh, archive_dir = env_test
    config = _base_config(archive_dir, compile_dir=str(repo_dir))

    # First run
    runner = ZpRunner(repo_dir)
    result1 = runner.run_test("release", config=config,
                              test_config=_TEST_CONFIG,
                              log_path=fix_log_path,
                              fail_on="ignore")
    assert not find_errors(result1.events), f"Run 1 errors: {find_errors(result1.events)}"

    persist_dir = archive_dir / TAG
    assert persist_dir.exists()
    files_before = set(f.name for f in fs.list_files(persist_dir))
    assert files_before, "First run should persist files"

    # Cleanup release for second run (same commit, same archive_dir)
    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)
    git._run("tag", "-d", TAG, check=False)
    git._run("push", "origin", f":refs/tags/{TAG}", check=False)

    # Second run with overwrite accepted
    result2 = runner.run_test("release", config=config,
                              test_config=_TEST_CONFIG,
                              log_path=fix_log_path,
                              fail_on="ignore")
    assert not find_errors(result2.events), f"Run 2 errors: {find_errors(result2.events)}"

    # Files should still be there (overwritten)
    files_after = set(f.name for f in fs.list_files(persist_dir))
    assert files_after == files_before, \
        f"Same files expected after overwrite. Before: {files_before}, After: {files_after}"


def test_persist_overwrite_refused(env_test, fix_log_path):
    """Second run with confirm_persist_overwrite=no: should fail/warn."""
    repo_dir, git, gh, archive_dir = env_test
    config = _base_config(archive_dir, compile_dir=str(repo_dir))

    # First run
    runner = ZpRunner(repo_dir)
    result1 = runner.run_test("release", config=config,
                              test_config=_TEST_CONFIG,
                              log_path=fix_log_path,
                              fail_on="ignore")
    assert not find_errors(result1.events), f"Run 1 errors: {find_errors(result1.events)}"

    # Cleanup release for second run
    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)
    git._run("tag", "-d", TAG, check=False)
    git._run("push", "origin", f":refs/tags/{TAG}", check=False)

    # Second run with overwrite REFUSED
    refuse_config = {
        "prompts": {**_PROMPTS, "confirm_persist_overwrite": "no"},
        "verify_prompts": False,
    }
    result2 = runner.run_test("release", config=config,
                              test_config=refuse_config,
                              log_path=fix_log_path,
                              fail_on="ignore")

    # Should have skipped persist
    assert find_by_name(result2.events, "persist.skipped"), \
        f"Expected persist.skipped when refusing overwrite. events={result2.events}"
