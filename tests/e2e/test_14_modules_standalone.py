"""Test: standalone module system (zp modules list / run).

Tests the `zp modules` CLI subcommand:
  - Module listing (built-in and project modules)
  - Standalone module execution (built-in and project)
  - Env var propagation (ZP_DEBUG, ZP_TEST_MODE, ZP_TEST_CONFIG)
  - NDJSON event relay through ZP output system
  - Debug mode (cmd/debug events shown only with --debug)

Uses a dummy module written on-the-fly to <tmp_path>/.zp/modules/dummy_module/.
No external repo needed — all tests use tmp_path.
"""

from pathlib import Path

from tests.utils.cli import ZpRunner
from tests.utils.git import GitClient
from tests.utils.ndjson import find_by_name


# ---------------------------------------------------------------------------
# Dummy module source (with standalone "greet" subcommand)
# ---------------------------------------------------------------------------

DUMMY_MODULE_PYPROJECT = '''\
[project]
name = "dummy-module"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []
'''

DUMMY_MODULE_SOURCE = '''\
"""Dummy ZP module with standalone subcommand for testing."""

import argparse
import json
import os
import sys


def emit(type_: str, msg: str, name: str = "", **kwargs):
    event = {"type": type_, "msg": msg, "name": name}
    if kwargs:
        event["data"] = kwargs
    print(json.dumps(event), flush=True)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")

    # Pipeline subcommands
    check_p = sub.add_parser("check")
    check_p.add_argument("--config")
    run_p = sub.add_parser("run")
    run_p.add_argument("--input", required=True)

    # Standalone subcommand
    greet_p = sub.add_parser("greet")
    greet_p.add_argument("name", nargs="?", default="world")

    # Standalone subcommand that prints plain text (non-NDJSON)
    plain_p = sub.add_parser("plain")

    args = parser.parse_args()

    if args.command == "check":
        emit("detail_ok", "check ok", name="dummy_module.check.ok")
        return

    if args.command == "run":
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
        emit("detail_ok", "run ok", name="dummy_module.run.ok")
        print(json.dumps({"type": "result", "files": []}), flush=True)
        return

    if args.command == "greet":
        emit("detail", f"Hello {args.name}", name="dummy_module.greet")
        emit("cmd", "echo hello", name="dummy_module.greet.cmd")
        emit("debug", "debug info", name="dummy_module.greet.debug")
        emit("detail_ok", "done", name="dummy_module.greet.done",
             zp_debug=os.environ.get("ZP_DEBUG", ""),
             zp_test_mode=os.environ.get("ZP_TEST_MODE", ""),
             zp_test_config=os.environ.get("ZP_TEST_CONFIG", ""))
        return

    if args.command == "plain":
        print("This is plain text")
        print("Not NDJSON at all")
        emit("detail_ok", "after plain", name="dummy_module.plain.done")
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
'''


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _install_dummy_module(base_dir: Path) -> Path:
    """Write dummy module to <base_dir>/.zp/modules/dummy_module/."""
    module_dir = base_dir / ".zp" / "modules" / "dummy_module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "pyproject.toml").write_text(DUMMY_MODULE_PYPROJECT)
    (module_dir / "dummy_module.py").write_text(DUMMY_MODULE_SOURCE)
    return module_dir


def _init_project(tmp_path: Path) -> Path:
    """Create a minimal ZP project (git repo + .zp.yaml)."""
    (tmp_path / ".zp.yaml").write_text("project_name:\n  prefix: test\n")
    git = GitClient.init(tmp_path)
    git.add_file(".gitkeep", "")
    git.add_and_commit("init")
    return tmp_path


# ---------------------------------------------------------------------------
# zp modules list
# ---------------------------------------------------------------------------

def test_modules_list_builtin(tmp_path, fix_log_path):
    """zp modules list shows built-in modules (digicert_timestamp)."""
    project = _init_project(tmp_path)
    runner = ZpRunner(project)
    result = runner.run("modules", "--test-mode", "list", log_path=fix_log_path)

    assert result.returncode == 0, f"modules list failed: {result.stderr}"
    assert "digicert_timestamp" in result.stdout, \
        f"Expected digicert_timestamp in list output. Got:\n{result.stdout}"


def test_modules_list_project_module(tmp_path, fix_log_path):
    """zp modules list shows project modules from .zp/modules/."""
    project = _init_project(tmp_path)
    _install_dummy_module(project)

    runner = ZpRunner(project)
    result = runner.run("modules", "--test-mode", "list", log_path=fix_log_path)

    assert result.returncode == 0, f"modules list failed: {result.stderr}"
    assert "dummy_module" in result.stdout, \
        f"Expected dummy_module in list output. Got:\n{result.stdout}"


# ---------------------------------------------------------------------------
# zp modules run (built-in)
# ---------------------------------------------------------------------------

def test_modules_run_builtin_help(tmp_path, fix_log_path):
    """zp modules run digicert_timestamp --help shows help and exits 0."""
    project = _init_project(tmp_path)
    runner = ZpRunner(project)
    result = runner.run("modules", "--test-mode", "run", "digicert_timestamp", "--help",
                        log_path=fix_log_path)

    assert result.returncode == 0, f"--help failed: {result.stderr}"


# ---------------------------------------------------------------------------
# zp modules run (project module)
# ---------------------------------------------------------------------------

def test_modules_run_project_module(tmp_path, fix_log_path):
    """zp modules run dummy_module greet: events relayed with source_type=module."""
    project = _init_project(tmp_path)
    _install_dummy_module(project)

    runner = ZpRunner(project)
    result = runner.run("modules", "--test-mode", "run", "dummy_module", "greet", "tester",
                        log_path=fix_log_path)

    assert result.returncode == 0, f"greet failed (rc={result.returncode}): {result.stderr}"

    greet = find_by_name(result.events, "dummy_module.greet")
    assert greet is not None, \
        f"Expected dummy_module.greet event. Events: {result.events}"
    assert greet["msg"] == "Hello tester", \
        f"Expected 'Hello tester'. Got: {greet['msg']}"
    assert greet.get("source_type") == "module", \
        f"Expected source_type='module'. Got: {greet.get('source_type')}"
    assert greet.get("source") == "dummy_module", \
        f"Expected source='dummy_module'. Got: {greet.get('source')}"

    done = find_by_name(result.events, "dummy_module.greet.done")
    assert done is not None, \
        f"Expected dummy_module.greet.done event. Events: {result.events}"


def test_modules_run_not_found(tmp_path, fix_log_path):
    """zp modules run nonexistent_module errors with module.error."""
    project = _init_project(tmp_path)
    runner = ZpRunner(project)
    result = runner.run("modules", "--test-mode", "run", "nonexistent_module", "greet",
                        log_path=fix_log_path)

    assert result.returncode != 0, "Should fail for non-existent module"
    assert find_by_name(result.events, "module.error"), \
        f"Expected module.error event. Events: {result.events}"


# ---------------------------------------------------------------------------
# Env vars (ZP_DEBUG, ZP_TEST_MODE, ZP_TEST_CONFIG)
# ---------------------------------------------------------------------------

def test_modules_run_debug_env(tmp_path, fix_log_path):
    """--debug sets ZP_DEBUG=1 in module subprocess environment."""
    project = _init_project(tmp_path)
    _install_dummy_module(project)

    runner = ZpRunner(project)
    result = runner.run("modules", "--test-mode", "--debug", "run",
                        "dummy_module", "greet",
                        log_path=fix_log_path)

    assert result.returncode == 0, f"Failed: {result.stderr}"
    done = find_by_name(result.events, "dummy_module.greet.done")
    assert done is not None, f"Expected greet.done event. Events: {result.events}"
    assert done.get("data", {}).get("zp_debug") == "1", \
        f"Expected ZP_DEBUG=1 in module env. Got data: {done.get('data')}"


def test_modules_run_test_mode_env(tmp_path, fix_log_path):
    """--test-mode sets ZP_TEST_MODE=1 in module subprocess environment."""
    project = _init_project(tmp_path)
    _install_dummy_module(project)

    runner = ZpRunner(project)
    result = runner.run("modules", "--test-mode", "run", "dummy_module", "greet",
                        log_path=fix_log_path)

    assert result.returncode == 0, f"Failed: {result.stderr}"
    done = find_by_name(result.events, "dummy_module.greet.done")
    assert done is not None, f"Expected greet.done event. Events: {result.events}"
    assert done.get("data", {}).get("zp_test_mode") == "1", \
        f"Expected ZP_TEST_MODE=1 in module env. Got data: {done.get('data')}"


def test_modules_run_test_config_env(tmp_path, fix_log_path):
    """--test-config sets ZP_TEST_CONFIG path in module subprocess environment."""
    project = _init_project(tmp_path)
    _install_dummy_module(project)

    config_file = tmp_path / "test.config.yaml"
    config_file.write_text("{}")

    runner = ZpRunner(project)
    result = runner.run("modules", "--test-mode", "--test-config", str(config_file),
                        "run", "dummy_module", "greet",
                        log_path=fix_log_path)

    assert result.returncode == 0, f"Failed: {result.stderr}"
    done = find_by_name(result.events, "dummy_module.greet.done")
    assert done is not None, f"Expected greet.done event. Events: {result.events}"
    assert done.get("data", {}).get("zp_test_config") == str(config_file), \
        f"Expected ZP_TEST_CONFIG={config_file}. Got data: {done.get('data')}"


# ---------------------------------------------------------------------------
# Debug output (ZP relay of cmd/debug events)
# ---------------------------------------------------------------------------

def test_modules_run_debug_shows_cmd(tmp_path, fix_log_path):
    """With --debug, events of type 'cmd' are present in the event stream."""
    project = _init_project(tmp_path)
    _install_dummy_module(project)

    runner = ZpRunner(project)
    result = runner.run("modules", "--test-mode", "--debug", "run",
                        "dummy_module", "greet",
                        log_path=fix_log_path)

    assert result.returncode == 0
    cmd_event = find_by_name(result.events, "dummy_module.greet.cmd")
    assert cmd_event is not None, \
        f"Expected cmd event in debug mode. Events: {result.events}"
    assert cmd_event["type"] == "cmd"
    assert cmd_event["msg"] == "echo hello"


def test_modules_run_no_debug_hides_cmd(tmp_path, fix_log_path):
    """Without --debug, events of type 'cmd' are still in test-mode NDJSON stream
    but hidden from human output."""
    project = _init_project(tmp_path)
    _install_dummy_module(project)

    # Run without --debug but with --test-mode
    runner = ZpRunner(project)
    result = runner.run("modules", "--test-mode", "run", "dummy_module", "greet",
                        log_path=fix_log_path)

    assert result.returncode == 0
    # In test mode, all events are in the NDJSON stream regardless of debug
    cmd_event = find_by_name(result.events, "dummy_module.greet.cmd")
    assert cmd_event is not None, \
        f"cmd event should be in NDJSON stream even without debug. Events: {result.events}"

    # But in human mode (no --test-mode, no --debug), cmd should be hidden
    result_human = runner.run("modules", "run", "dummy_module", "greet",
                              log_path=fix_log_path)
    assert result_human.returncode == 0
    assert "$ echo hello" not in result_human.stdout, \
        f"cmd event should be hidden without --debug in human mode. Got:\n{result_human.stdout}"


# ---------------------------------------------------------------------------
# NDJSON relay
# ---------------------------------------------------------------------------

def test_modules_run_ndjson_relayed(tmp_path, fix_log_path):
    """Module NDJSON events are relayed by ZP with source_type and source."""
    project = _init_project(tmp_path)
    _install_dummy_module(project)

    runner = ZpRunner(project)
    result = runner.run("modules", "--test-mode", "run", "dummy_module", "greet", "world",
                        log_path=fix_log_path)

    assert result.returncode == 0
    all_module_events = [e for e in result.events if e.get("source_type") == "module"]
    assert len(all_module_events) >= 2, \
        f"Expected at least 2 module events (greet + done). Got: {all_module_events}"

    for event in all_module_events:
        assert event.get("source") == "dummy_module", \
            f"All module events should have source='dummy_module'. Got: {event}"


def test_modules_run_non_ndjson_wrapped(tmp_path, fix_log_path):
    """Non-NDJSON lines from module are wrapped as detail events with source=module_name."""
    project = _init_project(tmp_path)
    _install_dummy_module(project)

    runner = ZpRunner(project)
    result = runner.run("modules", "--test-mode", "run", "dummy_module", "plain",
                        log_path=fix_log_path)

    assert result.returncode == 0

    # Plain text lines should be wrapped as detail events
    plain_events = [e for e in result.events
                    if e.get("msg") in ("This is plain text", "Not NDJSON at all")]
    assert len(plain_events) == 2, \
        f"Expected 2 wrapped plain text events. Got: {plain_events}"
    for event in plain_events:
        assert event.get("source_type") == "module", \
            f"Wrapped plain text should have source_type='module'. Got: {event}"
        assert event.get("source") == "dummy_module", \
            f"Wrapped plain text should have source='dummy_module'. Got: {event}"

    # NDJSON event should also be relayed
    done = find_by_name(result.events, "dummy_module.plain.done")
    assert done is not None, \
        f"Expected dummy_module.plain.done event. Events: {result.events}"
