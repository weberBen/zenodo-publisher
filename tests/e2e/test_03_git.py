"""Test: git repository checks (branch, sync, modifications, tags).

Uses the real test repo (from .zenodo.test.env) with auto-reset via repo_env.
Runs `zp release` and verifies git-related error detection.
"""

from tests.utils.cli import ZpRunner
from tests.utils.ndjson import find_errors, find_by_name, has_step_ok


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RELEASE_PROMPTS = {
    "enter_tag": "v1.0.0",
    "release_title": "",
    "release_notes": "",
    "confirm_build": "yes",
    "confirm_publish": "no",
}

_TEST_CONFIG = {"prompts": RELEASE_PROMPTS, "verify_prompts": False}


def _assert_has_error(result, name: str | None = None, msg_contains: str | None = None):
    errors = find_errors(result.events)
    assert errors, f"Expected error events, got none. events={result.events}"
    if name:
        assert find_by_name(result.events, name), \
            f"Expected event with name='{name}', got: {errors}"
    if msg_contains:
        assert any(msg_contains.lower() in e.get("msg", "").lower() for e in errors), \
            f"Expected error containing '{msg_contains}', got: {errors}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# --- Working tree dirty ---

def test_uncommitted_changes(repo_env, fix_log_path):
    """Modified tracked file not committed: should error git.local_modifications."""
    repo_dir, _ = repo_env
    (repo_dir / ".gitkeep").write_text("modified")

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    _assert_has_error(result, name="git.local_modifications")


def test_untracked_file(repo_env, fix_log_path):
    """Untracked file in working tree: should error git.local_modifications."""
    repo_dir, _ = repo_env
    (repo_dir / "untracked_test_file.txt").write_text("untracked")

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    _assert_has_error(result, name="git.local_modifications")


# --- Branch ---

def test_wrong_branch(repo_env, fix_log_path):
    """On a non-main branch: should error git.not_on_main."""
    repo_dir, git = repo_env
    git.branch_checkout("test-wrong-branch", create=True)

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    _assert_has_error(result, name="git.not_on_main")


# --- Sync with remote ---

def test_unpushed_commits(repo_env, fix_log_path):
    """Local commit not pushed: should error git.unpushed_commits."""
    repo_dir, git = repo_env
    git.add_file("unpushed_test_file.txt", "content")
    git.add_and_commit("local only commit")

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    _assert_has_error(result, name="git.unpushed_commits")


def test_unpushed_tags(repo_env, fix_log_path):
    """Local tag not pushed to remote: should error git.unpushed_tags."""
    repo_dir, git = repo_env
    git.tag_create("v99.99.99-test")

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    _assert_has_error(result, name="git.unpushed_tags")


# --- Happy path ---

def test_clean_repo(repo_env, fix_log_path):
    """Clean repo, on main, synced with remote: should pass git checks."""
    repo_dir, _ = repo_env

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "git.branch_check"), \
        f"Branch check should pass. events={result.events}"
    assert has_step_ok(result.events, "git.up_to_date"), \
        f"Up-to-date check should pass. events={result.events}"
