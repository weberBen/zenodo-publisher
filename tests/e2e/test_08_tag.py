"""Test: tag and release creation, validation, and edge cases.

Uses the real test repo (repo_env). Tests that ZP correctly creates
releases, detects tag conflicts, and handles various tag/release states.
"""

import tempfile
from pathlib import Path

import pytest

from tests import conftest
from tests.utils.cli import ZpRunner
from tests.utils.github import GithubClient
from tests.utils.ndjson import find_by_name, find_errors, find_data


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TAG = "v-test-tag"
TAG2 = "v-test-tag-2"
TAG3 = "v-test-tag-3"


def _base_config(archive_dir: Path, **overrides) -> dict:
    config = {
        "project_name": {"prefix": "TestProject", "suffix": "-{tag_name}"},
        "main_branch": "main",
        "compile": {"enabled": False},
        "signing": {"sign": False},
        "hash_algorithms": ["sha256"],
        "archive": {"format": "zip", "dir": str(archive_dir)},
        "prompt_validation_level": "danger",
        "github": {"check_draft": True},
        "generated_files": {
            "project": {"publishers": {"destination": {"file": []}}},
        },
    }
    config.update(overrides)
    return config


def _prompts(tag: str) -> dict:
    return {
        "prompts": {
            "enter_tag": tag,
            "release_title": "",
            "release_notes": "",
            "confirm_build": "yes",
            "confirm_publish": "no",
            "confirm_persist_overwrite": "yes",
        },
        "verify_prompts": False,
    }


# Prompts when release already exists (no enter_tag/title/notes)
_EXISTING_RELEASE_CONFIG = {
    "prompts": {
        "confirm_build": "yes",
        "confirm_publish": "no",
        "confirm_persist_overwrite": "yes",
    },
    "verify_prompts": False,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _cleanup_tags(git, gh):
    """Remove test releases and tags (remote + local)."""
    for tag in (TAG, TAG2, TAG3):
        if gh.has_release(tag):
            gh.delete_release(tag, cleanup_tag=True)
        git._run("tag", "-d", tag, check=False)
        git._run("push", "origin", f":refs/tags/{tag}", check=False)


@pytest.fixture
def tag_env(fix_repo_dir, fix_repo_git):
    """Yield (repo_dir, git, gh, archive_dir). Manual reset to control order."""
    repo_dir = fix_repo_dir
    git = fix_repo_git
    gh = GithubClient(repo_dir)

    # 1. Cleanup releases+tags FIRST (while tags still exist locally)
    _cleanup_tags(git, gh)
    # 2. Then reset repo (which deletes all local tags)
    conftest.reset_test_repo()

    archive_dir = Path(tempfile.mkdtemp())

    yield repo_dir, git, gh, archive_dir

    # Same order: cleanup releases before reset
    _cleanup_tags(git, gh)
    conftest.reset_test_repo()


# ---------------------------------------------------------------------------
# Tests: release creation
# ---------------------------------------------------------------------------

def test_create_release(tag_env, fix_log_path):
    """ZP should create a new release with a lightweight tag."""
    repo_dir, git, gh, archive_dir = tag_env
    config = _base_config(archive_dir)

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_prompts(TAG),
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    # Release should exist on GitHub
    assert gh.has_release(TAG), f"Release {TAG} should exist on GitHub"

    # Tag should be lightweight (not annotated)
    tag_info = gh.get_tag_info(TAG)
    assert tag_info["type"] == "lightweight", \
        f"Tag should be lightweight. Got: {tag_info}"


def test_existing_release_reused(tag_env, fix_log_path):
    """If latest commit already has a release, ZP should reuse it (no new tag)."""
    repo_dir, git, gh, archive_dir = tag_env
    config = _base_config(archive_dir)

    # First run: create release
    runner = ZpRunner(repo_dir)
    result1 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG),
                              log_path=fix_log_path,
                              fail_on="ignore")
    errors = find_errors(result1.events)
    assert not errors, f"Run 1 errors: {errors}"
    assert gh.has_release(TAG)

    # Second run: should detect existing release, no prompts for tag
    result2 = runner.run_test("release", config=config,
                              test_config=_EXISTING_RELEASE_CONFIG,
                              log_path=fix_log_path,
                              fail_on="ignore")
    errors = find_errors(result2.events)
    assert not errors, f"Run 2 errors: {errors}"

    assert find_by_name(result2.events, "release.existing"), \
        f"Expected release.existing event. events={result2.events}"


# ---------------------------------------------------------------------------
# Tests: tag already exists on remote
# ---------------------------------------------------------------------------

def test_tag_exists_same_commit(tag_env, fix_log_path):
    """Tag already exists on remote pointing to HEAD: ZP should accept it."""
    repo_dir, git, gh, archive_dir = tag_env
    config = _base_config(archive_dir)

    # Pre-create the tag on remote (pointing to HEAD)
    git.tag_create(TAG)
    git._run("push", "origin", TAG)

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_prompts(TAG),
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    # Should warn that tag exists but accept it
    assert find_by_name(result.events, "git.tag_exists"), \
        f"Expected git.tag_exists warning. events={result.events}"


def test_tag_exists_wrong_commit(tag_env, fix_log_path):
    """Tag exists on remote pointing to a different commit: ZP should reject."""
    repo_dir, git, gh, archive_dir = tag_env
    config = _base_config(archive_dir)

    # Create tag on current commit, push, then make a new commit
    git.tag_create(TAG)
    git._run("push", "origin", TAG)

    git.add_file("new_file_for_tag_test.txt", "content")
    git.add_and_commit("advance HEAD past tag")
    git.push()

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_prompts(TAG),
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert errors, f"Expected error for tag on wrong commit. events={result.events}"
    assert find_by_name(result.events, "git.tag_invalid"), \
        f"Expected git.tag_invalid. Got: {errors}"


# ---------------------------------------------------------------------------
# Tests: tag delete/recreate scenarios
# ---------------------------------------------------------------------------

def test_delete_remote_tag_keep_local_recreate(tag_env, fix_log_path):
    """Delete remote tag (keep local): ZP should first reject (unpushed tag),
    then succeed after pushing the tag."""
    repo_dir, git, gh, archive_dir = tag_env
    config = _base_config(archive_dir)

    # Create release first
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_prompts(TAG),
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert not find_errors(result.events)
    assert gh.has_release(TAG)

    # Fetch tag locally (gh release create only creates it on remote)
    git._run("fetch", "origin", "--tags")
    assert TAG in git.list_tags(), "Tag should exist locally after fetch"

    # Delete release + remote tag, keep local tag
    gh.delete_release(TAG, cleanup_tag=False)
    git._run("push", "origin", f":refs/tags/{TAG}")
    assert TAG in git.list_tags()

    # ZP should reject: local tag not pushed to remote
    result2 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG),
                              log_path=fix_log_path,
                              fail_on="ignore")
    errors = find_errors(result2.events)
    assert find_by_name(result2.events, "git.unpushed_tags"), \
        f"Expected unpushed_tags error. Got: {errors}"

    # Push the tag, then ZP should succeed
    git._run("push", "origin", TAG)
    result3 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG),
                              log_path=fix_log_path,
                              fail_on="ignore")
    assert not find_errors(result3.events), f"Errors after push: {find_errors(result3.events)}"
    assert gh.has_release(TAG)


def test_delete_local_tag_keep_remote_fail(tag_env, fix_log_path):
    """Delete local tag (keep remote): ZP should detect tag on remote pointing to same commit."""
    repo_dir, git, gh, archive_dir = tag_env
    config = _base_config(archive_dir)

    # Create release
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_prompts(TAG),
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert not find_errors(result.events)

    # Delete local tag only (remote still has it)
    git._run("tag", "-d", TAG)
    assert TAG not in git.list_tags()

    # Re-run ZP: latest commit is already released → should reuse
    result2 = runner.run_test("release", config=config,
                              test_config=_EXISTING_RELEASE_CONFIG,
                              log_path=fix_log_path,
                              fail_on="ignore")
    errors = find_errors(result2.events)
    assert not errors, f"Unexpected errors: {errors}"
    assert find_by_name(result2.events, "release.existing")


# ---------------------------------------------------------------------------
# Tests: commit/release alignment
# ---------------------------------------------------------------------------

def test_new_commit_not_released(tag_env, fix_log_path):
    """After a new commit, ZP should detect HEAD is not released and prompt for new tag."""
    repo_dir, git, gh, archive_dir = tag_env
    config = _base_config(archive_dir)

    # Create first release
    runner = ZpRunner(repo_dir)
    result1 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG),
                              log_path=fix_log_path,
                              fail_on="ignore")
    assert not find_errors(result1.events)
    assert gh.has_release(TAG)

    # New commit
    git.add_file("new_change.txt", "content")
    git.add_and_commit("post-release commit")
    git.push()

    # Second run: HEAD is not released, ZP should ask for a new tag
    result2 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG2),
                              log_path=fix_log_path,
                              fail_on="ignore")
    errors = find_errors(result2.events)
    assert not errors, f"Unexpected errors: {errors}"

    # New release should have been created
    assert gh.has_release(TAG2)
    # Both releases should exist
    assert gh.has_release(TAG)


def test_tag_on_old_commit(tag_env, fix_log_path):
    """Tag on an older (non-HEAD) commit: ZP should reject it as invalid."""
    repo_dir, git, gh, archive_dir = tag_env
    config = _base_config(archive_dir)

    # Get current HEAD sha
    old_sha = git.rev_parse("HEAD")

    # Make a new commit so HEAD advances
    git.add_file("advance.txt", "content")
    git.add_and_commit("advance past old commit")
    git.push()

    # Create a tag on the OLD commit (not HEAD)
    git._run("tag", TAG, old_sha)
    git._run("push", "origin", TAG)

    # ZP should reject: tag exists but points to wrong commit
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_prompts(TAG),
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert errors, f"Expected error for tag on old commit. events={result.events}"
    assert find_by_name(result.events, "git.tag_invalid"), \
        f"Expected git.tag_invalid. Got: {errors}"


def test_checkout_old_release(tag_env, fix_log_path):
    """On an old release's commit: ZP should detect it's already released (reuse)."""
    repo_dir, git, gh, archive_dir = tag_env
    config = _base_config(archive_dir)

    # Create first release
    runner = ZpRunner(repo_dir)
    result1 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG),
                              log_path=fix_log_path,
                              fail_on="ignore")
    assert not find_errors(result1.events)

    first_sha = git.rev_parse("HEAD")

    # New commit + new release
    git.add_file("newer.txt", "content")
    git.add_and_commit("second commit")
    git.push()

    result2 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG2),
                              log_path=fix_log_path,
                              fail_on="ignore")
    assert not find_errors(result2.events)
    assert gh.has_release(TAG2)

    second_sha = git.rev_parse("HEAD")
    assert first_sha != second_sha

    # The latest release is TAG2. ZP checks if latest commit is released.
    # HEAD points to TAG2's commit → should detect existing release
    result3 = runner.run_test("release", config=config,
                              test_config=_EXISTING_RELEASE_CONFIG,
                              log_path=fix_log_path,
                              fail_on="ignore")
    errors = find_errors(result3.events)
    assert not errors, f"Unexpected errors: {errors}"
    assert find_by_name(result3.events, "release.existing")


def test_technical_commit_after_release(tag_env, fix_log_path):
    """Commit with no content change after release: ZP should still require a new tag.

    Even if the commit is purely technical (README, CI, etc.), ZP only
    compares HEAD vs latest release commit — not file contents.
    """
    repo_dir, git, gh, archive_dir = tag_env
    config = _base_config(archive_dir)

    # Create release on current commit
    runner = ZpRunner(repo_dir)
    result1 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG),
                              log_path=fix_log_path,
                              fail_on="ignore")
    assert not find_errors(result1.events)
    assert gh.has_release(TAG)

    released_sha = git.rev_parse("HEAD")

    # Technical commit: only touch a non-published file (e.g. .gitignore comment)
    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    gitignore.write_text(existing + "\n# technical change\n")
    git.add_and_commit("chore: update gitignore comment")
    git.push()

    new_sha = git.rev_parse("HEAD")
    assert released_sha != new_sha, "HEAD should have advanced"

    # ZP should NOT detect existing release (HEAD != release commit)
    # → it should prompt for a new tag
    result2 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG2),
                              log_path=fix_log_path,
                              fail_on="ignore")
    errors = find_errors(result2.events)
    assert not errors, f"Unexpected errors: {errors}"

    # A new release should have been created, not reused
    assert gh.has_release(TAG2), "New release should exist for the technical commit"
    assert not find_by_name(result2.events, "release.existing"), \
        "Should NOT detect existing release — HEAD has changed"


def test_release_tag_moved_to_old_commit(tag_env, fix_log_path):
    """Release tag moved to an older commit on GitHub: ZP should require a new release.

    Simulates: someone edits the release on GitHub to point to an older commit.
    HEAD hasn't changed, but the release no longer points to it.
    """
    repo_dir, git, gh, archive_dir = tag_env
    config = _base_config(archive_dir)

    # Create two commits
    git.add_file("first.txt", "first")
    git.add_and_commit("first commit")
    git.push()
    old_sha = git.rev_parse("HEAD")

    git.add_file("second.txt", "second")
    git.add_and_commit("second commit")
    git.push()
    head_sha = git.rev_parse("HEAD")
    assert old_sha != head_sha

    # Create release on HEAD (current commit)
    runner = ZpRunner(repo_dir)
    result1 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG),
                              log_path=fix_log_path,
                              fail_on="ignore")
    assert not find_errors(result1.events)
    assert gh.has_release(TAG)

    # Move the tag to the OLD commit on GitHub:
    # Deleting a tag does NOT delete the associated release — the release
    # becomes a draft with an internal untagged-<hash> reference.
    # Re-creating the tag with the same name re-associates the release.
    git._run("push", "origin", f":refs/tags/{TAG}")
    git._run("tag", "-d", TAG, check=False)
    git._run("tag", TAG, old_sha)
    git._run("push", "origin", TAG)

    # Now: HEAD is on second commit, but release tag points to first commit.
    # ZP should see HEAD != latest release commit → ask for new tag.
    result2 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG2),
                              log_path=fix_log_path,
                              fail_on="ignore")
    errors = find_errors(result2.events)
    assert not errors, f"Unexpected errors: {errors}"

    assert not find_by_name(result2.events, "release.existing"), \
        "Should NOT detect existing release — tag was moved to old commit"
    assert gh.has_release(TAG2), \
        "New release should have been created"


def test_local_tag_same_sha_as_remote_tag_different_name(tag_env, fix_log_path):
    """Local tag with different name but same SHA as existing remote release tag.

    Create a release with TAG, then create a local tag TAG3 pointing to the
    same commit. Try to release with TAG3 — should fail because TAG3 points
    to the same commit as the existing release TAG (already released).
    """
    repo_dir, git, gh, archive_dir = tag_env
    config = _base_config(archive_dir)

    # Create release with TAG on current commit
    runner = ZpRunner(repo_dir)
    result1 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG),
                              log_path=fix_log_path,
                              fail_on="ignore")
    assert not find_errors(result1.events)
    assert gh.has_release(TAG)

    released_sha = git.rev_parse("HEAD")

    # Create local tag TAG3 pointing to the same commit (not pushed)
    git.tag_create(TAG3)
    assert TAG3 in git.list_tags()
    assert git.rev_parse(TAG3) == released_sha

    # ZP should reject: TAG3 is a local unpushed tag
    result2 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG3),
                              log_path=fix_log_path,
                              fail_on="ignore")
    assert find_by_name(result2.events, "git.unpushed_tags"), \
        f"Expected unpushed_tags error. Got: {find_errors(result2.events)}"

    # Push TAG3, then ZP should detect existing release (TAG on same commit)
    git._run("push", "origin", TAG3)
    result3 = runner.run_test("release", config=config,
                              test_config={
                                  "prompts": {
                                      "confirm_build": "yes",
                                      "confirm_publish": "no",
                                      "confirm_persist_overwrite": "yes",
                                  },
                                  "verify_prompts": False,
                              },
                              log_path=fix_log_path,
                              fail_on="ignore")
    errors = find_errors(result3.events)
    assert not errors, f"Unexpected errors: {errors}"

    assert find_by_name(result3.events, "release.existing"), \
        f"Expected release.existing — commit is already released via {TAG}. events={result3.events}"


def test_local_tag_same_sha_as_old_release(tag_env, fix_log_path):
    """Local tag on same commit as a previous (non-latest) release.

    Create release TAG, advance HEAD, create release TAG2 (latest).
    Then create local tag TAG3 pointing to TAG's commit (old release).
    ZP should NOT consider this as released — only the latest release counts.
    """
    repo_dir, git, gh, archive_dir = tag_env
    config = _base_config(archive_dir)
    runner = ZpRunner(repo_dir)

    # First release on current commit
    result1 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG),
                              log_path=fix_log_path,
                              fail_on="ignore")
    assert not find_errors(result1.events)
    old_sha = git.rev_parse("HEAD")

    # Advance HEAD, create second release (now latest)
    git.add_file("advance_for_tag3.txt", "content")
    git.add_and_commit("advance HEAD")
    git.push()

    result2 = runner.run_test("release", config=config,
                              test_config=_prompts(TAG2),
                              log_path=fix_log_path,
                              fail_on="ignore")
    assert not find_errors(result2.events)
    assert gh.has_release(TAG2)
    new_sha = git.rev_parse("HEAD")
    assert old_sha != new_sha

    # Create tag TAG3 pointing to the OLD commit (TAG's commit) and push it
    git._run("tag", TAG3, old_sha)
    assert git.rev_parse(TAG3) == old_sha
    git._run("push", "origin", TAG3)

    # ZP should detect that HEAD matches latest release (TAG2) → existing.
    # TAG3 pointing to old commit is irrelevant — ZP only checks latest release.
    result3 = runner.run_test("release", config=config,
                              test_config={
                                  "prompts": {
                                      "confirm_build": "yes",
                                      "confirm_publish": "no",
                                      "confirm_persist_overwrite": "yes",
                                  },
                                  "verify_prompts": False,
                              },
                              log_path=fix_log_path,
                              fail_on="ignore")
    errors = find_errors(result3.events)
    assert not errors, f"Unexpected errors: {errors}"
    assert find_by_name(result3.events, "release.existing"), \
        f"Expected release.existing — HEAD matches TAG2, TAG3 is irrelevant. events={result3.events}"


def test_draft_release_ignored(tag_env, fix_log_path):
    """A draft release should be ignored by ZP — it should create a new release."""
    repo_dir, _, gh, archive_dir = tag_env
    config = _base_config(archive_dir)

    # Create a draft release manually (not via ZP)
    gh._run("release", "create", TAG, "--title", TAG, "--notes", "draft test", "--draft")

    # ZP should NOT see the draft as an existing release
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_prompts(TAG2),
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    # ZP should have created a NEW release (TAG2), not reused the draft
    assert not find_by_name(result.events, "release.existing"), \
        "Draft release should not be detected as existing"
    assert gh.has_release(TAG2), \
        "ZP should have created a new non-draft release"


def test_draft_release_tag_reuse_refused(tag_env, fix_log_path):
    """Using a tag associated with a draft release: ZP should refuse.

    The tag exists on remote (created by the draft), so check_tag_validity
    will find it. Even though it points to HEAD, ZP should not reuse a
    tag that belongs to a draft release.
    """
    repo_dir, git, gh, archive_dir = tag_env
    config = _base_config(archive_dir)

    # Create a draft release with TAG (this also creates the tag on remote)
    gh._run("release", "create", TAG, "--title", TAG, "--notes", "draft", "--draft")

    # Fetch tag locally
    git._run("fetch", "origin", "--tags")

    # Try to use the same TAG for a real release
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_prompts(TAG),
                             log_path=fix_log_path,
                             fail_on="ignore")

    # ZP should detect the tag is associated with a draft release and refuse
    errors = find_errors(result.events)
    assert errors, f"Expected error for draft release tag. events={result.events}"
    assert find_by_name(result.events, "git.tag_draft_release"), \
        f"Expected git.tag_draft_release. Got: {errors}"


def test_draft_release_check_disabled(tag_env, fix_log_path):
    """With check_draft disabled: draft tag should be accepted (converted to published)."""
    repo_dir, _, gh, archive_dir = tag_env
    config = _base_config(archive_dir, github={"check_draft": False})

    # Create a draft release with TAG
    gh._run("release", "create", TAG, "--title", TAG, "--notes", "draft", "--draft")

    # With check_draft=False, ZP should not detect the draft and proceed
    # gh release create will silently convert the draft to a published release
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_prompts(TAG),
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"With check_draft=False, should succeed. Got: {errors}"

    # The draft should have been converted to a published release
    assert gh.has_release(TAG), "Draft should have been converted to published release"


