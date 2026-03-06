#!/usr/bin/env python3
"""E2E test runner for zenodo-publisher.

Runs test directories in alphabetical order. Each test directory contains:
  - Optional override files (zenodo_config.yaml, .zenodo.env, etc.)
  - test.config.yaml: CLI args + prompt responses for zp
  - test.py: verification script with a `run(result, repo_dir, ctx)` function

Usage:
    python tests/runner.py --work-dir /path/to/test-repo
    python tests/runner.py --work-dir /path/to/test-repo --start-from test_10_release

The test repo is a real GitHub repo that test_00_base resets to a known state.
"""

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path

import yaml

TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR.parent))

from tests.utils.cli import ZpRunner
from tests.utils.git import reset_repo_files, git_add_and_commit, git_push, git_remote_url
from tests.utils.ndjson import verify_prompts

TEST_CONFIG_FILENAME = "test.config.yaml"
TEST_PY_NAME = "test.py"
PROTECTED_FILES = [TEST_CONFIG_FILENAME, TEST_PY_NAME]


def discover_tests(tests_dir: Path) -> list[Path]:
    """Find all test_* directories, sorted alphabetically."""
    return sorted(
        d for d in tests_dir.iterdir()
        if d.is_dir() and d.name.startswith("test_")
    )


def apply_test_files(test_dir: Path, repo_dir: Path, base_dir: Path):
    """Apply test file overrides to the repo.

    For each file in test_dir (except test.py and test.config.yaml),
    copy it to repo_dir, overriding the base.
    Files not present in test_dir keep their base version.
    """
    # First reset to base
    reset_repo_files(repo_dir, base_dir)

    # Then apply test-specific overrides
    for item in test_dir.iterdir():
        if item.name in PROTECTED_FILES:
            continue
        dst = repo_dir / item.name
        if item.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(item, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst)


def load_test_config(test_dir: Path) -> dict | None:
    """Load test.config.yaml from a test directory."""
    config_file = test_dir / TEST_CONFIG_FILENAME
    if not config_file.exists():
        return None
    with open(config_file) as f:
        return yaml.safe_load(f) or {}


def load_test_module(test_dir: Path):
    """Dynamically import test.py from a test directory."""
    test_file = test_dir / "test.py"
    if not test_file.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"test_{test_dir.name}", test_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_single_test(test_dir: Path, repo_dir: Path, base_dir: Path,
                    runner: ZpRunner, ctx: dict) -> bool:
    """Run a single test. Returns True if passed."""
    name = test_dir.name
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")

    # Special case: test_00_base just resets the repo
    if name.startswith("test_00"):
        print("  Resetting repo to base template...")
        reset_repo_files(repo_dir, base_dir)
        git_add_and_commit(repo_dir, msg="reset to base template")
        git_push(repo_dir)
        print("  OK: repo reset")
        return True

    # Apply files (base + overrides)
    apply_test_files(test_dir, repo_dir, base_dir)

    # Load test config
    test_config = load_test_config(test_dir)
    if test_config is None:
        print(f"  SKIP: no {TEST_CONFIG_FILENAME}")
        return True

    cli_section = test_config.get("cli", {})
    command = cli_section.get("command")
    if not command:
        print(f"  SKIP: no cli.command in {TEST_CONFIG_FILENAME}")
        return True

    cli_args = cli_section.get("args", [])

    # Build the test config file path for --test-config
    test_config_path = test_dir / TEST_CONFIG_FILENAME
    prompts_section = test_config.get("prompts", {})

    # Run zp
    print(f"  Running: zp {command} --test-mode --test-config {test_config_path.name} {' '.join(cli_args)}")
    result = runner.run_with_config(command, cli_args,
                                     test_config_path=test_config_path)

    # Verify prompt exhaustivity
    if prompts_section and result.events:
        try:
            verify_prompts(result.events, set(prompts_section.keys()))
        except AssertionError as e:
            print(f"  FAIL: {name} (prompt verification)")
            print(f"    {e}")
            return False

    # Load and run test.py
    module = load_test_module(test_dir)
    if module is None:
        print(f"  SKIP: no test.py")
        return True

    if not hasattr(module, "run"):
        print(f"  SKIP: test.py has no run() function")
        return True

    try:
        module.run(result=result, repo_dir=repo_dir, ctx=ctx)
        print(f"  PASS: {name}")
        return True
    except AssertionError as e:
        print(f"  FAIL: {name}")
        print(f"    {e}")
        return False
    except Exception as e:
        print(f"  ERROR: {name}")
        print(f"    {type(e).__name__}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="E2E test runner for zp")
    parser.add_argument("--work-dir", required=True,
                        help="Path to the test git repo")
    parser.add_argument("--start-from", default=None,
                        help="Start from this test (skip earlier ones)")
    args = parser.parse_args()

    repo_dir = Path(args.work_dir).resolve()
    if not (repo_dir / ".git").exists():
        print(f"Error: {repo_dir} is not a git repository", file=sys.stderr)
        sys.exit(1)

    remote_url = git_remote_url(repo_dir)
    print(f"Repo URL: {remote_url}")

    tests = discover_tests(TESTS_DIR)
    if not tests:
        print("No test directories found", file=sys.stderr)
        sys.exit(1)

    base_dir = TESTS_DIR / "test_00_base"
    if not base_dir.exists():
        print("Error: test_00_base/ not found", file=sys.stderr)
        sys.exit(1)

    runner = ZpRunner(repo_dir)

    # Context shared across tests
    ctx = {
        "repo_dir": repo_dir,
        "tests_dir": TESTS_DIR,
    }

    # Filter if --start-from
    if args.start_from:
        skip = True
        filtered = []
        for t in tests:
            if t.name == args.start_from or args.start_from in t.name:
                skip = False
            if not skip:
                filtered.append(t)
        tests = filtered

    print(f"Running {len(tests)} test(s) on {repo_dir}")

    passed = 0
    failed = 0
    for test_dir in tests:
        if run_single_test(test_dir, repo_dir, base_dir, runner, ctx):
            passed += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed, {failed} failed")
    print(f"{'='*60}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
