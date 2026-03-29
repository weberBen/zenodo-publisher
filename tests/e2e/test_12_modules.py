"""Test: pipeline module system.

Two kinds of tests:
  - Config tests   (tmp_path)    — validate config loading, no external repo needed
  - Pipeline tests (release_env) — full pipeline run on the external test repo

A dummy module is written on-the-fly to <project_root>/.zp/modules/dummy_module/main.py.
It emits detail/detail_ok events and produces one .dummy file per input file
(module_entry_type="out"). Setting module_config.fail=true makes it exit(1).
"""

import json
import subprocess
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

DUMMY_MODULE_SOURCE = '''\
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
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
    parser.add_argument("--input", required=True)
    args = parser.parse_args()

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

        if module_cfg.get("fail"):
            emit("error", f"dummy_module: forced failure for \'{fp.name}\'",
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
            "publishers": {"destination": {}},
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
    """Write dummy module to <repo_dir>/.zp/modules/dummy_module/main.py."""
    module_dir = repo_dir / ".zp" / "modules" / "dummy_module"
    module_dir.mkdir(parents=True, exist_ok=True)
    module_path = module_dir / "main.py"
    module_path.write_text(DUMMY_MODULE_SOURCE)
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

    assert find_by_name(result.events, "dummy_module.processing"), \
        "Module should have processed files"
    assert find_by_name(result.events, "dummy_module.done"), \
        "Module should have produced output"


def test_module_confirm_prompt_asked(release_env, fix_log_path):
    """confirm_run_module prompt is asked before executing the module."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=_base_config(archive_dir),
                             test_config=_test_config(run_module="yes"),
                             log_path=fix_log_path)

    prompts = get_prompt_names(result.events)
    assert "confirm_run_module" in prompts, \
        f"confirm_run_module should have been asked. Prompted: {prompts}"


def test_module_skip_on_decline(release_env, fix_log_path):
    """Declining confirm_run_module skips the module; no output produced."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=_base_config(archive_dir),
                             test_config=_test_config(run_module="no"),
                             log_path=fix_log_path)

    assert find_by_name(result.events, "module.skipped"), \
        "Expected module.skipped event when user declines"
    assert not find_by_name(result.events, "dummy_module.processing"), \
        "Module should not have run after user declined"


def test_module_events_relayed(release_env, fix_log_path):
    """Events emitted by the module (detail, detail_ok) are relayed to ZP output."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=_base_config(archive_dir),
                             test_config=_test_config(),
                             log_path=fix_log_path)

    detail = find_by_name(result.events, "dummy_module.processing")
    assert detail is not None, "detail event from module not relayed to ZP output"
    assert detail.get("data", {}).get("config_key") == "project", \
        f"Module should receive 'project' entry. Got data: {detail.get('data')}"

    done = find_by_name(result.events, "dummy_module.done")
    assert done is not None, "detail_ok event from module not relayed"
    assert done.get("data", {}).get("filename", "").endswith(".dummy"), \
        f"Expected .dummy output filename. Got: {done.get('data')}"


def test_module_error_stops_pipeline(release_env, fix_log_path):
    """Module exiting non-zero raises a fatal error and stops the pipeline."""
    repo_dir, git, gh, archive_dir = release_env
    _install_dummy_module(repo_dir)

    config = _base_config(archive_dir, module_cfg={"fail": True})
    runner = ZpRunner(repo_dir)
    result = runner.run_test("release",
                             config=config,
                             test_config=_test_config(),
                             log_path=fix_log_path,
                             fail_on="ignore")

    assert find_by_name(result.events, "module.run_error"), \
        f"Expected module.run_error fatal. Errors: {find_errors(result.events)}"
    assert not find_by_name(result.events, "dummy_module.done"), \
        "Pipeline should have stopped before module completion"


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

    persist_dir = archive_dir / TAG
    names = [f.name for f in fs.list_files(persist_dir)] if persist_dir.exists() else []
    assert not any(n.endswith(".dummy") for n in names), \
        f"Expected .dummy NOT archived (entry_type mismatch). Got: {names}"


# ---------------------------------------------------------------------------
# Standalone uv test — no pipeline, no repo
# ---------------------------------------------------------------------------

def test_dummy_module_uv_standalone(tmp_path):
    """Dummy module runs correctly via uv run outside the ZP project."""
    module_path = tmp_path / "dummy_module.py"
    module_path.write_text(DUMMY_MODULE_SOURCE)

    fake_file = tmp_path / "fake_paper.pdf"
    fake_file.write_text("fake content")

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    input_data = {
        "config": {"identity_hash_algo": "sha256"},
        "output_dir": str(output_dir),
        "files": [
            {
                "file_path": str(fake_file),
                "config_key": "paper",
                "type": "file",
                "hashes": {"sha256": {"value": "abc123", "formatted_value": "sha256:abc123"}},
                "module_config": {},
            }
        ],
    }

    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps(input_data))

    proc = subprocess.run(
        ["uv", "run", str(module_path), "--input", str(input_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(tmp_path),  # outside ZP project
    )

    assert proc.returncode == 0, \
        f"Module exited with {proc.returncode}.\nstderr: {proc.stderr}\nstdout: {proc.stdout}"

    events = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    processing = next((e for e in events if e.get("name") == "dummy_module.processing"), None)
    assert processing is not None, f"Expected dummy_module.processing event. Got: {events}"
    assert processing.get("data", {}).get("filename") == "fake_paper.pdf", \
        f"Wrong filename in event data: {processing.get('data')}"

    done = next((e for e in events if e.get("name") == "dummy_module.done"), None)
    assert done is not None, f"Expected dummy_module.done event. Got: {events}"

    result_event = next((e for e in events if e.get("type") == "result"), None)
    assert result_event is not None, f"Expected result event. Got: {events}"

    files = result_event.get("files", [])
    assert len(files) == 1, f"Expected 1 result file. Got: {files}"
    assert files[0]["module_entry_type"] == "out", f"Wrong entry_type: {files[0]}"
    assert files[0]["config_key"] == "paper", f"Wrong config_key: {files[0]}"
    assert Path(files[0]["file_path"]).exists(), \
        f"Output file should exist on disk: {files[0]['file_path']}"
