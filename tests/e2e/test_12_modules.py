"""Test: pipeline module system.

Two kinds of tests:
  - Config tests   (tmp_path)    — validate config loading, no external repo needed
  - Pipeline tests (release_env) — full pipeline run on the external test repo

A dummy module is written on-the-fly to <project_root>/.zp/modules/dummy_module/.
It is a uv project directory (main.py + pyproject.toml with no dependencies).
It emits detail/detail_ok events and produces one .dummy file per input file
(module_entry_type="out"). Setting module_config.fail_check/fail_run makes it exit(1).
"""

import tempfile
from pathlib import Path

import pytest

from tests.utils.cli import ZpRunner
from tests.utils.git import GitClient
from tests.utils.github import GithubClient
from tests.utils.ndjson import (
    find_errors, find_by_name, get_prompt_names,
)
from tests.utils import fs


# ---------------------------------------------------------------------------
# Dummy module source
# ---------------------------------------------------------------------------

DUMMY_MODULE_PYPROJECT = '''\
[project]
name = "dummy-module"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []
'''

# Module that emits sys.prefix during --check, used to verify uv env isolation.
ISOLATION_MODULE_PYPROJECT = '''\
[project]
name = "isolation-module"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []
'''

ISOLATION_MODULE_SOURCE = '''\
"""Isolation test module — emits sys.prefix to verify the uv project env is used."""

import argparse
import json
import sys


def emit(type_, msg, name="", **kwargs):
    event = {"type": type_, "msg": msg, "name": name}
    if kwargs:
        event["data"] = kwargs
    print(json.dumps(event), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--config")
    parser.add_argument("--input")
    args = parser.parse_args()

    if args.check:
        emit("detail_ok", "isolation_module: check ok",
             name="isolation_module.check.ok",
             python_prefix=sys.prefix)
        return

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    emit("detail_ok", "isolation_module: run ok",
         name="isolation_module.run.ok",
         python_prefix=sys.prefix)
    print(json.dumps({"type": "result", "files": []}), flush=True)


if __name__ == "__main__":
    main()
'''

DUMMY_MODULE_SOURCE = '''\
"""Dummy ZP module for testing."""

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
        module_cfg = {}
        if args.config:
            with open(args.config, encoding="utf-8") as f:
                module_cfg = json.load(f).get("module_config", {})
        if module_cfg.get("fail_check"):
            emit("error", "dummy_module: check forced failure",
                 name="dummy_module.check.forced_error")
            sys.exit(1)
        emit("detail_ok", "dummy_module: check ok", name="dummy_module.check.ok")
        return

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    output_dir = Path(data["output_dir"])
    result_files = []

    for file_info in data["files"]:
        fp = Path(file_info["file_path"])
        module_cfg = file_info.get("module_config", {})

        emit("detail", f"dummy_module: processing \'{fp.name}\'",
             name="dummy_module.processing",
             filename=fp.name, config_key=file_info["config_key"])

        if module_cfg.get("fail_run"):
            emit("error", f"dummy_module: forced run failure for \'{fp.name}\'",
                 name="dummy_module.forced_error")
            sys.exit(1)

        out_path = output_dir / f"{fp.name}.dummy"
        out_path.write_text(f"dummy:{fp.name}")

        emit("detail_ok", f"dummy_module: produced {out_path.name}",
             name="dummy_module.done", filename=out_path.name)

        result_files.append({
            "file_path": str(out_path),
            "config_key": file_info["config_key"],
            "module_entry_type": "out",
            # Pass through publishers from module_config so tests can verify routing
            "publishers": {"destination": module_cfg.get("publishers", {})},
        })

    print(json.dumps({"type": "result", "files": result_files}), flush=True)


if __name__ == "__main__":
    main()
'''

TAG = "v-test-modules"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_init(directory: Path):
    git = GitClient.init(directory)
    git.add_file(".gitkeep", "")
    git.add_and_commit("init")


def _install_dummy_module(repo_dir: Path) -> Path:
    """Write dummy module to <repo_dir>/.zp/modules/dummy_module/ (uv project)."""
    module_dir = repo_dir / ".zp" / "modules" / "dummy_module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "pyproject.toml").write_text(DUMMY_MODULE_PYPROJECT)
    module_path = module_dir / "main.py"
    module_path.write_text(DUMMY_MODULE_SOURCE)
    return module_path


def _install_isolation_module(repo_dir: Path) -> Path:
    """Write isolation test module to <repo_dir>/.zp/modules/isolation_module/ (uv project)."""
    module_dir = repo_dir / ".zp" / "modules" / "isolation_module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "pyproject.toml").write_text(ISOLATION_MODULE_PYPROJECT)
    module_path = module_dir / "main.py"
    module_path.write_text(ISOLATION_MODULE_SOURCE)
    return module_path


def _base_config(archive_dir: Path, module_cfg: dict | None = None) -> dict:
    """Release config with project entry that runs dummy_module."""
    return {
        "project_name": {"prefix": "TestProject", "suffix": "-{tag_name}"},
        "main_branch": "main",
        "compile": {"enabled": False},
        "signing": {"sign": False},
        "hash_algorithms": ["sha256"],
        "archive": {"format": "zip", "dir": str(archive_dir), "types": ["file"]},
        "prompt_validation_level": "danger",
        "modules": {"dummy_module": module_cfg or {}},
        "generated_files": {
            "project": {
                "archive_types": ["file"],
                "publishers": {"destination": {"file": []}},
                "modules": {"dummy_module": {}},
            },
        },
    }


def _test_config(run_module: str = "yes") -> dict:
    return {
        "prompts": {
            "confirm_run_module": run_module,
            "confirm_publish": "no",
        },
        "verify_prompts": False,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def release_env(repo_env):
    """Create a tag + GitHub release, yield (repo_dir, git, gh, archive_dir), cleanup after."""
    repo_dir, git = repo_env
    gh = GithubClient(repo_dir)

    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)
    git._run("tag", "-d", TAG, check=False)
    git._run("push", "origin", f":refs/tags/{TAG}", check=False)

    git.tag_create(TAG)
    git._run("push", "origin", TAG)
    gh.create_release(TAG, title=TAG, body="test release")

    archive_dir = Path(tempfile.mkdtemp())
    yield repo_dir, git, gh, archive_dir

    gh.delete_release(TAG, cleanup_tag=True)


# ---------------------------------------------------------------------------
# Config tests (no external repo needed)
# ---------------------------------------------------------------------------

def test_module_not_found(tmp_path, fix_log_path):
    """Module declared in config but not found anywhere: config error."""
    _git_init(tmp_path)
    config = {
        "project_name": {"prefix": "X", "suffix": ""},
        "main_branch": "main",
        "compile": {"enabled": False},
        "signing": {"sign": False},
        "hash_algorithms": ["sha256"],
        "prompt_validation_level": "danger",
        "modules": {"nonexistent_module": {}},
        "generated_files": {
            "project": {"publishers": {"destination": {"file": []}}},
        },
    }
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config, log_path=fix_log_path, fail_on="ignore")
    assert find_by_name(result.events, "config_error.loading.config.modules.not_found"), \
        f"Expected config_error.loading.config.modules.not_found error. Got: {find_errors(result.events)}"


# ---------------------------------------------------------------------------
# Pipeline tests (require release_env)
# ---------------------------------------------------------------------------

def test_module_discovered_in_project_root(release_env, fix_log_path):
    """Dummy module in project_root/.zp/modules/ is discovered and runs."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=_base_config(archive_dir),
                             test_config=_test_config(),
                             log_path=fix_log_path)

    found = find_by_name(result.events, "module.found")
    assert found is not None, "ZP should emit module.found"
    assert found.get("data", {}).get("module_name") == "dummy_module"
    # origin should point to .zp/modules/dummy_module (not built-in)
    assert "built-in" not in found.get("data", {}).get("origin", ""), \
        f"Expected project-root origin, got: {found.get('data', {}).get('origin')}"

    assert find_by_name(result.events, "dummy_module.processing"), \
        "Module should have processed files"
    assert find_by_name(result.events, "dummy_module.done"), \
        "Module should have produced output"
    assert find_by_name(result.events, "module.done"), \
        "ZP should emit module.done after module returns result files"


def test_module_confirm_prompt_asked(release_env, fix_log_path):
    """confirm_run_module prompt is asked before executing; module.confirmed follows acceptance."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=_base_config(archive_dir),
                             test_config=_test_config(run_module="yes"),
                             log_path=fix_log_path)

    prompts_asked = get_prompt_names(result.events)
    assert "confirm_run_module" in prompts_asked, \
        f"confirm_run_module should have been asked. Prompted: {prompts_asked}"
    assert find_by_name(result.events, "module.confirmed"), \
        "ZP should emit module.confirmed after user accepts"


def test_module_skip_on_decline(release_env, fix_log_path):
    """Declining confirm_run_module skips the module; check still ran, no execution."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=_base_config(archive_dir),
                             test_config=_test_config(run_module="no"),
                             log_path=fix_log_path)

    # Check phase still ran before the prompt
    assert find_by_name(result.events, "module.found"), \
        "ZP should have found the module before prompting"
    assert find_by_name(result.events, "module.check_ok"), \
        "Module check should have passed before the confirm prompt"
    # User declined: module.confirmed must NOT appear, module.skipped must appear
    assert not find_by_name(result.events, "module.confirmed"), \
        "module.confirmed must not appear when user declines"
    assert find_by_name(result.events, "module.skipped"), \
        "Expected module.skipped when user declines"
    assert not find_by_name(result.events, "dummy_module.processing"), \
        "Module must not have run after user declined"


def test_module_events_relayed(release_env, fix_log_path):
    """Events emitted by the module are relayed; ZP's own module.done logs the result count."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=_base_config(archive_dir),
                             test_config=_test_config(),
                             log_path=fix_log_path)

    # Module events relayed verbatim
    processing = find_by_name(result.events, "dummy_module.processing")
    assert processing is not None, "dummy_module.processing not relayed to ZP output"
    assert processing.get("data", {}).get("config_key") == "project", \
        f"Module should receive 'project' entry. Got data: {processing.get('data')}"

    module_done = find_by_name(result.events, "dummy_module.done")
    assert module_done is not None, "dummy_module.done not relayed"
    assert module_done.get("data", {}).get("filename", "").endswith(".dummy"), \
        f"Expected .dummy output filename. Got: {module_done.get('data')}"

    # ZP's own module.done event with result count
    zp_done = find_by_name(result.events, "module.done")
    assert zp_done is not None, "ZP should emit module.done after module returns"
    assert zp_done.get("data", {}).get("module_name") == "dummy_module", \
        f"module.done should carry module_name. Got: {zp_done.get('data')}"
    assert zp_done.get("data", {}).get("n") == 1, \
        f"module.done should carry n=1 (one input file → one output). Got: {zp_done.get('data')}"


def test_module_check_error_stops_pipeline(release_env, fix_log_path):
    """Module failing --check aborts the pipeline at MODULE_CHECK, before git check."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    config = _base_config(archive_dir, module_cfg={"fail_check": True})
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=config,
                             test_config=_test_config(),
                             log_path=fix_log_path,
                             fail_on="ignore")

    assert find_by_name(result.events, "module.found"), \
        "ZP should have found the module before attempting check"
    assert find_by_name(result.events, "module.checking"), \
        "ZP should have emitted module.checking before the failure"
    assert find_by_name(result.events, "module.check_failed"), \
        f"Expected module.check_failed fatal. Errors: {find_errors(result.events)}"
    assert not find_by_name(result.events, "git.up_to_date"), \
        "Pipeline should have stopped before git check"


def test_module_run_error_stops_pipeline(release_env, fix_log_path):
    """Module exiting non-zero during run: check passed, confirmed, running, then fatal error."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    config = _base_config(archive_dir, module_cfg={"fail_run": True})
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=config,
                             test_config=_test_config(),
                             log_path=fix_log_path,
                             fail_on="ignore")

    # Check phase passed
    assert find_by_name(result.events, "module.check_ok"), \
        "module.check_ok should appear (check passes even if run will fail)"
    # Run phase started
    assert find_by_name(result.events, "module.confirmed"), \
        "module.confirmed should appear before the failed run"
    assert find_by_name(result.events, "module.running"), \
        "module.running should appear before the failed run"
    # Fatal error
    assert find_by_name(result.events, "module.run_error"), \
        f"Expected module.run_error fatal. Errors: {find_errors(result.events)}"
    assert not find_by_name(result.events, "module.done"), \
        "module.done must not appear when run fails"


def test_module_archive_by_module_name(release_env, fix_log_path):
    """archive_types: [dummy_module] archives all outputs of the module."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    config = _base_config(archive_dir)
    config["generated_files"]["project"]["archive_types"] = ["file", "dummy_module"]

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=config,
                             test_config=_test_config(),
                             log_path=fix_log_path)

    done = find_by_name(result.events, "module.done")
    assert done is not None, "ZP should emit module.done"
    assert done.get("data", {}).get("n", 0) >= 1, \
        f"module.done should report at least 1 result file. Got: {done.get('data')}"

    persist_dir = archive_dir / TAG
    assert persist_dir.exists(), f"Persist dir not created: {persist_dir}"
    names = [f.name for f in fs.list_files(persist_dir)]
    assert any(n.endswith(".dummy") for n in names), \
        f"Expected .dummy file archived. Got: {names}"


def test_module_archive_by_entry_type(release_env, fix_log_path):
    """archive_types: [dummy_module.out] archives only outputs with module_entry_type='out'."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    config = _base_config(archive_dir)
    config["generated_files"]["project"]["archive_types"] = ["file", "dummy_module.out"]

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=config,
                             test_config=_test_config(),
                             log_path=fix_log_path)

    assert find_by_name(result.events, "module.done"), \
        "ZP should emit module.done confirming files were received"

    persist_dir = archive_dir / TAG
    names = [f.name for f in fs.list_files(persist_dir)]
    assert any(n.endswith(".dummy") for n in names), \
        f"Expected .dummy archived via entry_type match. Got: {names}"


def test_module_no_archive_wrong_entry_type(release_env, fix_log_path):
    """archive_types: [dummy_module.other] does NOT archive outputs with entry_type='out'."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    config = _base_config(archive_dir)
    # "other" does not match — dummy module emits "out"
    config["generated_files"]["project"]["archive_types"] = ["file", "dummy_module.other"]

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=config,
                             test_config=_test_config(),
                             log_path=fix_log_path)

    # Module still ran and returned files (ZP received them)
    done = find_by_name(result.events, "module.done")
    assert done is not None, "ZP should emit module.done even when entry_type doesn't match archive"
    assert done.get("data", {}).get("n", 0) >= 1, \
        "module.done should report files received, even if none get archived"

    persist_dir = archive_dir / TAG
    names = [f.name for f in fs.list_files(persist_dir)] if persist_dir.exists() else []
    assert not any(n.endswith(".dummy") for n in names), \
        f"Expected .dummy NOT archived (entry_type mismatch). Got: {names}"


# ---------------------------------------------------------------------------
# Publisher destination routing tests (symmetric to archive tests)
# ---------------------------------------------------------------------------

def _find_github_upload(events, suffix: str):
    """Return the github.asset_uploaded event for a file ending with suffix, or None."""
    for e in events:
        if e.get("name") == "github.asset_uploaded":
            if e.get("data", {}).get("filename", "").endswith(suffix):
                return e
    return None


def test_module_destination_by_module_name(release_env, fix_log_path):
    """publishers.destination: {dummy_module: [github]} routes all module outputs to GitHub."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    config = _base_config(archive_dir, module_cfg={"publishers": {"dummy_module": ["github"]}})
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=config,
                             test_config=_test_config(),
                             log_path=fix_log_path)

    assert _find_github_upload(result.events, ".dummy"), \
        f"Expected .dummy uploaded to GitHub (dummy_module → github). Events: {result.events}"


def test_module_destination_by_entry_type(release_env, fix_log_path):
    """publishers.destination: {dummy_module.out: [github]} routes only entry_type='out' to GitHub."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    config = _base_config(archive_dir, module_cfg={"publishers": {"dummy_module.out": ["github"]}})
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=config,
                             test_config=_test_config(),
                             log_path=fix_log_path)

    assert _find_github_upload(result.events, ".dummy"), \
        f"Expected .dummy uploaded to GitHub (dummy_module.out → github). Events: {result.events}"


def test_module_no_destination_wrong_entry_type(release_env, fix_log_path):
    """publishers.destination: {dummy_module.other: [github]} does NOT route entry_type='out' to GitHub."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    # "other" does not match — dummy module emits "out"
    config = _base_config(archive_dir, module_cfg={"publishers": {"dummy_module.other": ["github"]}})
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=config,
                             test_config=_test_config(),
                             log_path=fix_log_path)

    assert not _find_github_upload(result.events, ".dummy"), \
        "Expected .dummy NOT uploaded to GitHub (entry_type mismatch)"


# ---------------------------------------------------------------------------
# FileEntry content test
# ---------------------------------------------------------------------------

def test_module_entry_content(release_env, fix_log_path):
    """ZP builds FileEntry with correct properties from module output: module_name,
    module_entry_type, config_key, archive flag, publishers destination."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    # publishers are passed via module_config so the dummy module includes them in its output
    module_publishers = {"github": []}
    config = _base_config(archive_dir, module_cfg={"publishers": module_publishers})
    config["generated_files"]["project"]["archive_types"] = ["file", "dummy_module"]

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=config,
                             test_config=_test_config(),
                             log_path=fix_log_path)

    entry = find_by_name(result.events, "module.entry")
    assert entry is not None, \
        f"ZP should emit module.entry for each module result file. Got: {result.events}"

    data = entry.get("data", {})
    assert data.get("module_name") == "dummy_module", \
        f"module.entry should carry module_name. Got: {data}"
    assert data.get("module_entry_type") == "out", \
        f"module.entry should carry module_entry_type='out' (as declared by dummy module). Got: {data}"
    assert data.get("config_key") == "project", \
        f"module.entry should carry config_key matching the parent generated_files entry. Got: {data}"
    assert data.get("archive") is True, \
        f"module.entry should have archive=True ('dummy_module' is in archive_types). Got: {data}"
    assert data.get("publishers") == module_publishers, \
        f"module.entry should carry the publishers returned by the module. Got: {data}"


# ---------------------------------------------------------------------------
# uv project env isolation test
# ---------------------------------------------------------------------------

def test_module_uv_project_env_isolation(release_env, fix_log_path):
    """Module runs in its own uv project env (not ZP's or the system env).

    The module emits sys.prefix during --check. We verify it points inside the
    module's own directory (i.e. <module_dir>/.venv), not inside ZP's project
    or any system Python prefix.
    """
    repo_dir, git, gh, archive_dir = release_env
    module_dir = _install_isolation_module(repo_dir).parent

    config = _base_config(archive_dir)
    config["modules"] = {"isolation_module": {}}
    config["generated_files"]["project"]["modules"] = {"isolation_module": {}}

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=config,
                             test_config=_test_config(),
                             log_path=fix_log_path)

    check_ok = find_by_name(result.events, "isolation_module.check.ok")
    assert check_ok is not None, \
        f"isolation_module.check.ok not relayed by ZP. Errors: {find_errors(result.events)}"

    python_prefix = check_ok.get("data", {}).get("python_prefix", "")
    assert str(module_dir.resolve()) in python_prefix, (
        f"Expected sys.prefix inside module dir {module_dir}.\n"
        f"Got sys.prefix: {python_prefix}\n"
        "This means uv did NOT use the module's isolated project env."
    )


# ---------------------------------------------------------------------------
# ZP ↔ module interface tests
# ---------------------------------------------------------------------------

def test_module_result_files_received_by_zp(release_env, fix_log_path):
    """ZP collects result files from module: module.found → module.running → module.done → archived."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    config = _base_config(archive_dir)
    config["generated_files"]["project"]["archive_types"] = ["file", "dummy_module"]

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=config,
                             test_config=_test_config(),
                             log_path=fix_log_path)

    assert find_by_name(result.events, "module.found"), \
        "ZP should emit module.found when the module is located"
    assert find_by_name(result.events, "module.confirmed"), \
        "ZP should emit module.confirmed after user accepts"
    assert find_by_name(result.events, "module.running"), \
        "ZP should emit module.running before invoking the module"
    assert find_by_name(result.events, "module.done"), \
        "ZP should emit module.done after receiving module result files"

    persist_dir = archive_dir / TAG
    assert persist_dir.exists(), f"Persist dir not created: {persist_dir}"
    names = [f.name for f in fs.list_files(persist_dir)]
    assert any(n.endswith(".dummy") for n in names), \
        f"ZP did not archive module result files. Archived: {names}"


def test_module_check_ok_event_relayed_by_zp(release_env, fix_log_path):
    """ZP MODULE_CHECK lifecycle: module.found → module.checking → [module relay] → module.check_ok."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=_base_config(archive_dir),
                             test_config=_test_config(),
                             log_path=fix_log_path)

    found = find_by_name(result.events, "module.found")
    assert found is not None, "ZP should emit module.found when the module is located"
    assert found.get("data", {}).get("module_name") == "dummy_module"

    assert find_by_name(result.events, "module.checking"), \
        "ZP should emit module.checking before running --check"
    assert find_by_name(result.events, "dummy_module.check.ok"), \
        "ZP should relay the check.ok event emitted by the module"

    check_ok = find_by_name(result.events, "module.check_ok")
    assert check_ok is not None, "ZP should emit module.check_ok after the module passes --check"
    assert check_ok.get("data", {}).get("module_name") == "dummy_module", \
        f"module.check_ok should carry module_name. Got: {check_ok.get('data')}"


def test_module_check_failure_relayed_by_zp(release_env, fix_log_path):
    """ZP MODULE_CHECK failure: module.found → module.checking → [error relay] → module.check_failed."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    config = _base_config(archive_dir, module_cfg={"fail_check": True})
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=config,
                             test_config=_test_config(),
                             log_path=fix_log_path,
                             fail_on="ignore")

    assert find_by_name(result.events, "module.found"), \
        "ZP should emit module.found before attempting check"
    assert find_by_name(result.events, "module.checking"), \
        "ZP should emit module.checking before running --check"
    assert find_by_name(result.events, "dummy_module.check.forced_error"), \
        "ZP should relay the error event emitted by the module during failed --check"
    assert find_by_name(result.events, "module.check_failed"), \
        "ZP should emit module.check_failed after the module --check exits non-zero"

