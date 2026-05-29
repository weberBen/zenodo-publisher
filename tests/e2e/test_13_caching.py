"""Test: pipeline caching and resume.

All tests use release_env (external repo with real GitHub release).
The pipeline is crashed at controlled points via `fail_after_step` in test config.

Scenarios covered:
  - Cache dir created on crash, deleted on success
  - Resume from a built-in step (hash)
  - Resume skips compile (confirm_build not re-triggered)
  - Resume from module crash (last checkpoint = manifest, CUSTOM_MODULES re-runs)
  - Archived files not regenerated on resume (archive events absent in run 2)
  - Cache discarded (user answers no → fresh run)
  - Cache dir is tag-specific (.zp/cache/{tag_name}/)
"""

import tempfile
from pathlib import Path
import shutil

import pytest

from tests.utils.cli import ZpRunner
from tests.utils.git import GitClient
from tests.utils.github import GithubClient
from tests.utils.ndjson import find_by_name, find_errors, get_prompt_names

TAG = "v-test-caching"

# ---------------------------------------------------------------------------
# Module source (same pattern as test_12)
# ---------------------------------------------------------------------------

_DUMMY_MODULE_PYPROJECT = '''\
[project]
name = "dummy-module"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []
'''

_DUMMY_MODULE_SOURCE = '''\
"""Dummy ZP module for caching tests."""

import argparse
import json
import sys
from pathlib import Path


def emit(type_: str, msg: str, name: str = "", **kwargs):
    event = {"type": type_, "msg": msg, "name": name}
    if kwargs:
        event["data"] = kwargs
    print(json.dumps(event), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--config")
    args = parser.parse_args()

    if args.check:
        emit("detail_ok", "dummy_module: check ok", name="dummy_module.check.ok")
        return

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    module_cfg = {}
    for file_info in data["files"]:
        module_cfg = file_info.get("module_config", {})
        break

    output_dir = Path(data["output_dir"])

    fail_flag = module_cfg.get("fail_flag")
    if fail_flag and Path(fail_flag).exists():
        emit("error", "dummy_module: forced run failure", name="dummy_module.forced_error")
        sys.exit(1)

    result_files = []
    for file_info in data["files"]:
        fp = Path(file_info["file_path"])
        out_path = output_dir / f"{fp.name}.dummy"
        out_path.write_text(f"dummy:{fp.name}")
        emit("detail_ok", f"dummy_module: produced {out_path.name}",
             name="dummy_module.done", filename=out_path.name)
        result_files.append({
            "file_path": str(out_path),
            "config_key": file_info["config_key"],
            "module_entry_type": "out",
        })

    print(json.dumps({"type": "result", "files": result_files}), flush=True)


if __name__ == "__main__":
    main()
'''


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _install_dummy_module(repo_dir: Path) -> None:
    module_dir = repo_dir / ".zp" / "modules" / "dummy_module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "pyproject.toml").write_text(_DUMMY_MODULE_PYPROJECT)
    (module_dir / "dummy_module.py").write_text(_DUMMY_MODULE_SOURCE)


def _cache_dir(repo_dir: Path) -> Path:
    return repo_dir / ".zp" / "cache" / TAG


def _checkpoint_file(repo_dir: Path) -> Path:
    return _cache_dir(repo_dir) / ".zp_checkpoint.pkl"


def _base_config(archive_dir: Path) -> dict:
    return {
        "project_name": {"prefix": "TestProject", "suffix": "-{tag_name}"},
        "main_branch": "main",
        "compile": {"enabled": False},
        "signing": {"sign": False},
        "hash_algorithms": ["sha256"],
        "archive": {"format": "zip", "dir": str(archive_dir)},
        "prompt_validation_level": "danger",
        "pipeline": {"caching": True},
        "generated_files": {
            "project": {"publishers": {"destination": {"file": []}}},
        },
    }


def _crash_config(fail_after_step: str, extra_prompts: dict | None = None) -> dict:
    prompts = {"confirm_publish": "no", **(extra_prompts or {})}
    return {
        "prompts": prompts,
        "fail_after_step": fail_after_step,
        "verify_prompts": False,
    }


def _resume_config(answer: str = "yes", extra_prompts: dict | None = None) -> dict:
    prompts = {"confirm_resume": answer, "confirm_publish": "no", **(extra_prompts or {})}
    return {
        "prompts": prompts,
        "verify_prompts": False,
    }


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def release_env(repo_env):
    """Create TAG + GitHub release. Clean up cache dir + release after test."""
    repo_dir, git = repo_env
    gh = GithubClient(repo_dir)

    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)
    git._run("tag", "-d", TAG, check=False)
    git._run("push", "origin", f":refs/tags/{TAG}", check=False)

    git.tag_create(TAG)
    git._run("push", "origin", TAG)
    gh.create_release(TAG, title=TAG, body="caching test")

    archive_dir = Path(tempfile.mkdtemp())

    yield repo_dir, git, gh, archive_dir

    # Explicit cache dir cleanup (git reset may not remove gitignored dirs)
    cache = _cache_dir(repo_dir)
    if cache.exists():
        shutil.rmtree(cache, ignore_errors=True)

    gh.delete_release(TAG, cleanup_tag=True)


# ---------------------------------------------------------------------------
# Tests: cache dir lifecycle
# ---------------------------------------------------------------------------

def test_cache_dir_created_on_crash(release_env, fix_log_path):
    """Cache dir + checkpoint are created when pipeline crashes mid-run."""
    repo_dir, git, gh, archive_dir = release_env
    runner = ZpRunner(repo_dir)

    result = runner.run_test(
        "release",
        config=_base_config(archive_dir),
        test_config=_crash_config("hash"),
        log_path=fix_log_path,
        fail_on="ignore",
    )

    assert not result.ok, "Process should have exited non-zero after crash injection"
    assert find_by_name(result.events, "test.crash.hash"), "Expected test.crash.hash event"
    assert _cache_dir(repo_dir).exists(), "Cache dir should exist after crash"
    assert _checkpoint_file(repo_dir).exists(), "Checkpoint file should exist after crash"


def test_cache_deleted_on_success(release_env, fix_log_path):
    """Cache dir is deleted after a successful full pipeline run."""
    repo_dir, git, gh, archive_dir = release_env
    runner = ZpRunner(repo_dir)

    result = runner.run_test(
        "release",
        config=_base_config(archive_dir),
        test_config={"prompts": {"confirm_publish": "no"}, "verify_prompts": False},
        log_path=fix_log_path,
    )

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"
    assert not _cache_dir(repo_dir).exists(), "Cache dir should be deleted after successful run"


# ---------------------------------------------------------------------------
# Tests: resume from built-in step
# ---------------------------------------------------------------------------

def test_resume_from_builtin_step(release_env, fix_log_path):
    """Crash after hash, resume: pipeline completes, cache dir deleted."""
    repo_dir, git, gh, archive_dir = release_env
    runner = ZpRunner(repo_dir)

    # Run 1: crash after hash
    run1 = runner.run_test(
        "release",
        config=_base_config(archive_dir),
        test_config=_crash_config("hash"),
        log_path=fix_log_path,
        fail_on="ignore",
    )
    assert not run1.ok
    assert find_by_name(run1.events, "test.crash.hash"), "Expected test.crash.hash event"
    assert _checkpoint_file(repo_dir).exists()

    # Run 2: resume
    run2 = runner.run_test(
        "release",
        config=_base_config(archive_dir),
        test_config=_resume_config("yes"),
        log_path=fix_log_path,
    )

    errors = find_errors(run2.events)
    assert not errors, f"Resume produced errors: {errors}"
    assert find_by_name(run2.events, "cache.resume"), "Expected cache.resume event"
    assert not _cache_dir(repo_dir).exists(), "Cache dir should be deleted after successful resume"


def test_discard_cache(release_env, fix_log_path):
    """Crash after archive, user says 'no' to resume → fresh run succeeds."""
    repo_dir, git, gh, archive_dir = release_env
    runner = ZpRunner(repo_dir)

    # Run 1: crash after archive
    run1 = runner.run_test(
        "release",
        config=_base_config(archive_dir),
        test_config=_crash_config("archive"),
        log_path=fix_log_path,
        fail_on="ignore",
    )
    assert not run1.ok
    assert find_by_name(run1.events, "test.crash.archive"), "Expected test.crash.archive event"
    assert _cache_dir(repo_dir).exists()

    # Run 2: discard cache, fresh start
    run2 = runner.run_test(
        "release",
        config=_base_config(archive_dir),
        test_config=_resume_config("no"),
        log_path=fix_log_path,
    )

    errors = find_errors(run2.events)
    assert not errors, f"Fresh run after discard produced errors: {errors}"
    assert find_by_name(run2.events, "cache.discard"), "Expected cache.discard event"
    assert not _cache_dir(repo_dir).exists(), "Cache dir should be gone after fresh run"


# ---------------------------------------------------------------------------
# Tests: archive files not regenerated on resume
# ---------------------------------------------------------------------------

def test_no_regen_after_archive_crash(release_env, fix_log_path):
    """Resume after archive crash: ARCHIVE step not re-run (no archive events in run 2)."""
    repo_dir, git, gh, archive_dir = release_env
    runner = ZpRunner(repo_dir)

    # Run 1: crash after archive step
    run1 = runner.run_test(
        "release",
        config=_base_config(archive_dir),
        test_config=_crash_config("archive"),
        log_path=fix_log_path,
        fail_on="ignore",
    )
    assert not run1.ok
    assert find_by_name(run1.events, "test.crash.archive"), "Expected test.crash.archive event"
    # Archive events should be in run 1
    assert find_by_name(run1.events, "archive.project"), \
        "Expected archive.project event in run 1"

    # Run 2: resume — ARCHIVE already done, should not run again
    run2 = runner.run_test(
        "release",
        config=_base_config(archive_dir),
        test_config=_resume_config("yes"),
        log_path=fix_log_path,
    )

    errors = find_errors(run2.events)
    assert not errors, f"Resume produced errors: {errors}"
    assert not find_by_name(run2.events, "archive.project"), \
        "archive.project should NOT fire on resume (archive already done)"
    assert not find_by_name(run2.events, "archive.copy"), \
        "archive.copy should NOT fire on resume (archive already done)"


# ---------------------------------------------------------------------------
# Tests: compile not re-run on resume
# ---------------------------------------------------------------------------

def test_resume_skips_compile(release_env, fix_log_path):
    """Crash after compile: on resume, compile is skipped (no confirm_build prompt)."""
    repo_dir, git, gh, archive_dir = release_env

    # Set up Makefile (gitignore build output to keep repo clean)
    makefile = (
        ".PHONY: deploy\n"
        "deploy:\n"
        "\tmkdir -p build\n"
        "\techo 'compiled content' > build/output.txt\n"
    )
    (repo_dir / "Makefile").write_text(makefile)
    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    gitignore.write_text(existing + "\nbuild/\n")
    git = GitClient(repo_dir)
    git.add_and_commit("add Makefile and gitignore for caching test")
    git.push()
    git._run("tag", "-d", TAG)
    git._run("tag", TAG)
    git._run("push", "--force", "origin", TAG)

    config = _base_config(archive_dir)
    config["compile"] = {"enabled": True, "dir": str(repo_dir)}
    config["generated_files"] = {
        "paper": {
            "pattern": "build/output.txt",
            "publishers": {"destination": {"file": []}},
        },
        "project": {"publishers": {"destination": {"file": []}}},
    }

    runner = ZpRunner(repo_dir)

    # Run 1: build + crash after compile
    run1 = runner.run_test(
        "release",
        config=config,
        test_config=_crash_config("compile", extra_prompts={"confirm_build": "yes"}),
        log_path=fix_log_path,
        fail_on="ignore",
    )
    assert not run1.ok
    assert find_by_name(run1.events, "test.crash.compile"), "Expected test.crash.compile event"
    assert _checkpoint_file(repo_dir).exists()
    # Compiled file should be in cache dir
    assert (repo_dir / "build" / "output.txt").exists(), \
        "Makefile should have produced build/output.txt"

    # Run 2: resume — compile step skipped, no confirm_build prompt
    run2 = runner.run_test(
        "release",
        config=config,
        test_config=_resume_config("yes"),
        log_path=fix_log_path,
    )

    errors = find_errors(run2.events)
    assert not errors, f"Resume produced errors: {errors}"

    prompts_run2 = get_prompt_names(run2.events)
    assert "confirm_build" not in prompts_run2, \
        f"compile should be skipped on resume, but confirm_build was triggered. prompts={prompts_run2}"


# ---------------------------------------------------------------------------
# Tests: compile crash (make failure) → compile re-runs on resume
# ---------------------------------------------------------------------------

def test_compile_crash_reruns_compile(release_env, fix_log_path):
    """Compile step fails (make exits non-zero): checkpoint is at project_name.
    On resume, confirm_build is re-prompted (compile was not completed).
    """
    repo_dir, git, gh, archive_dir = release_env

    makefile_fail = (
        ".PHONY: deploy\n"
        "deploy:\n"
        "\tfalse\n"
    )
    makefile_ok = (
        ".PHONY: deploy\n"
        "deploy:\n"
        "\tmkdir -p build\n"
        "\techo 'compiled content' > build/output.txt\n"
    )
    (repo_dir / "Makefile").write_text(makefile_fail)
    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    gitignore.write_text(existing + "\nbuild/\n")
    git = GitClient(repo_dir)
    git.add_and_commit("add failing Makefile")
    git.push()
    git._run("tag", "-d", TAG)
    git._run("tag", TAG)
    git._run("push", "--force", "origin", TAG)

    config = _base_config(archive_dir)
    config["compile"] = {"enabled": True, "dir": str(repo_dir)}
    config["generated_files"] = {
        "paper": {
            "pattern": "build/output.txt",
            "publishers": {"destination": {"file": []}},
        },
        "project": {"publishers": {"destination": {"file": []}}},
    }

    runner = ZpRunner(repo_dir)

    # Run 1: make fails inside COMPILE → no checkpoint written for compile
    run1 = runner.run_test(
        "release",
        config=config,
        test_config={"prompts": {"confirm_build": "yes"}, "verify_prompts": False},
        log_path=fix_log_path,
        fail_on="ignore",
    )
    assert not run1.ok, "Pipeline should fail when make exits non-zero"
    assert _checkpoint_file(repo_dir).exists(), "Checkpoint should exist (at project_name)"

    # Fix Makefile, retag to new commit
    (repo_dir / "Makefile").write_text(makefile_ok)
    git.add_and_commit("fix Makefile")
    git.push()
    git._run("tag", "-d", TAG)
    git._run("tag", TAG)
    git._run("push", "--force", "origin", TAG)

    # Run 2: resume — compile not in checkpoint → confirm_build re-prompted
    run2 = runner.run_test(
        "release",
        config=config,
        test_config={
            "prompts": {
                "confirm_resume": "yes",
                "confirm_build": "yes",
                "confirm_publish": "no",
            },
            "verify_prompts": False,
        },
        log_path=fix_log_path,
    )

    errors = find_errors(run2.events)
    assert not errors, f"Resume with fixed compile produced errors: {errors}"
    prompts_run2 = get_prompt_names(run2.events)
    assert "confirm_build" in prompts_run2, \
        "compile should re-run on resume (not in checkpoint)"
    assert not _cache_dir(repo_dir).exists(), "Cache dir should be deleted after success"


# ---------------------------------------------------------------------------
# Tests: module crash and resume
# ---------------------------------------------------------------------------

def test_module_crash_and_resume(release_env, fix_log_path):
    """Module fails (exit 1) during CUSTOM_MODULES: checkpoint at manifest preserved.
    On resume, module runs successfully.
    """
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    # Flag file on disk controls whether the module fails.
    # Config stays identical between runs — only the file's existence changes.
    flag_file = Path(tempfile.mktemp(suffix=".flag"))
    flag_file.touch()

    config = _base_config(archive_dir)
    config["modules"] = {"dummy_module": {"fail_flag": str(flag_file)}}
    config["generated_files"] = {
        "project": {
            "publishers": {"destination": {"file": []}},
            "modules": {"dummy_module": {}},
        },
    }

    runner = ZpRunner(repo_dir)

    # Run 1: module crashes during CUSTOM_MODULES (flag file exists)
    run1 = runner.run_test(
        "release",
        config=config,
        test_config={
            "prompts": {
                "confirm_run_module": "yes",
                "confirm_publish": "no",
            },
            "verify_prompts": False,
        },
        log_path=fix_log_path,
        fail_on="ignore",
    )
    assert not run1.ok, "Pipeline should fail when module crashes"
    assert _cache_dir(repo_dir).exists(), "Cache dir should exist after module crash"
    assert _checkpoint_file(repo_dir).exists(), "Checkpoint should be preserved (at manifest)"

    # "Fix" the module: remove the flag file so it succeeds on resume.
    # The config is unchanged — the checkpoint will restore the same config,
    # but the flag file no longer exists so the module runs cleanly.
    flag_file.unlink(missing_ok=True)

    run2 = runner.run_test(
        "release",
        config=config,
        test_config={
            "prompts": {
                "confirm_resume": "yes",
                "confirm_run_module": "yes",
                "confirm_publish": "no",
            },
            "verify_prompts": False,
        },
        log_path=fix_log_path,
    )

    errors = find_errors(run2.events)
    assert not errors, f"Resume with fixed module produced errors: {errors}"
    assert find_by_name(run2.events, "modules.completed"), \
        "Expected modules.completed event on resume"
    assert find_by_name(run2.events, "dummy_module.done"), \
        "Expected dummy_module.done event: module should run on resume"
    assert not _cache_dir(repo_dir).exists(), "Cache dir should be deleted after success"


# ---------------------------------------------------------------------------
# Tests: tag isolation
# ---------------------------------------------------------------------------

def test_cache_dir_is_tag_specific(release_env, fix_log_path):
    """Cache dir is created under .zp/cache/{tag_name}/, not under another tag."""
    repo_dir, git, gh, archive_dir = release_env

    runner = ZpRunner(repo_dir)

    result = runner.run_test(
        "release",
        config=_base_config(archive_dir),
        test_config=_crash_config("hash"),
        log_path=fix_log_path,
        fail_on="ignore",
    )

    assert not result.ok
    assert find_by_name(result.events, "test.crash.hash"), "Expected test.crash.hash event"
    expected_cache = repo_dir / ".zp" / "cache" / TAG
    assert expected_cache.exists(), f"Cache dir should be at {expected_cache}"

    # No other tag dirs should exist under .zp/cache/
    cache_base = repo_dir / ".zp" / "cache"
    tag_dirs = [d for d in cache_base.iterdir() if d.is_dir()]
    assert tag_dirs == [expected_cache], \
        f"Only {TAG} cache dir should exist, found: {[d.name for d in tag_dirs]}"
