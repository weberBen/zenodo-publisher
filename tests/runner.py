#!/usr/bin/env python3
"""E2E test runner for zenodo-publisher.

Runs test directories in alphabetical order. Each test directory contains:
  - Optional override files (zenodo_config.yaml, .zenodo.env, etc.)
  - test.config.yaml: CLI args + prompt responses for zp
  - test.py: verification script with a `run(result, repo_dir, ctx)` function

Usage:
    python tests/runner.py --work-dir /path/to/test-repo
    python tests/runner.py --work-dir /path/to/test-repo --start-from test_10_release

The test repo is a real GitHub repo that test_00 resets to a known state.
"""

import argparse
import importlib.util
import sys
from pathlib import Path

import yaml

TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR.parent))

REPO_CONFIG_FILENAME = "zenodo_config.yaml"
TEST_ENV_FILENAME = ".zenodo.test.env"
BASE_TEST_DIR_NAME = "test_00_reset"

from tests.utils.cli import ZpRunner
from tests.utils.git import GitClient
from tests.utils.ndjson import verify_prompts

TEST_CONFIG_FILENAME = "test.config.yaml"
TEST_PY_NAME = "test.py"
PROTECTED_FILES = [TEST_CONFIG_FILENAME, TEST_PY_NAME]


class AttrDict(dict):
    """Dict with attribute access."""
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


def load_test_env(tests_dir: Path) -> dict[str, str]:
    """Load .zenodo.test.env from tests dir. Returns empty dict if missing."""
    path = tests_dir / TEST_ENV_FILENAME
    if not path.exists():
        raise Exception(f"Test file config is mendatory '{path}' does not exists")
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"')
    return env


def load_repo_config(repo_dir: Path) -> AttrDict | None:
    """Load zenodo_config.yaml from repo, or None if missing."""
    path = repo_dir / REPO_CONFIG_FILENAME
    if not path.exists():
        return None
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return None
    return AttrDict(data)


def discover_tests(tests_dir: Path) -> list[Path]:
    """Find all test_* directories, sorted alphabetically."""
    return sorted(
        d for d in tests_dir.iterdir()
        if d.is_dir() and d.name.startswith("test_")
    )


def _extract_test_num(test_dir: Path) -> str | None:
    """Extract the numeric prefix from a test dir name (e.g. 'test_01_foo' -> '01')."""
    parts = test_dir.name.split("_", 2)
    if len(parts) >= 2 and parts[1].isdigit():
        return parts[1]
    return None


def filter_tests(tests: list[Path], range_spec: str) -> list[Path]:
    """Filter tests by range spec.

    Formats: '01' (single), '01-12' (range), '01-*' (from 01), '*-07' (up to 07).
    """
    if "-" in range_spec:
        start, end = range_spec.split("-", 1)
    else:
        start, end = range_spec, range_spec

    filtered = []
    for t in tests:
        num = _extract_test_num(t)
        if num is None:
            continue
        if start != "*" and num < start.zfill(len(num)):
            continue
        if end != "*" and num > end.zfill(len(num)):
            continue
        filtered.append(t)
    return filtered


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

    # Special case: test_00 — run test.py directly (no zp run)
    if name.startswith("test_00"):
        ctx["repo_config"] = load_repo_config(repo_dir)
        module = load_test_module(test_dir)
        if module and hasattr(module, "run"):
            try:
                ctx["result"] = None
                module.run(ctx)
                print(f"  PASS: {name}")
            except AssertionError as e:
                print(f"  FAIL: {name}")
                print(f"    {e}")
                return False
            except Exception as e:
                print(f"  ERROR: {name}")
                print(f"    {type(e).__name__}: {e}")
                return False
        else:
            print(f"  SKIP: no test.py or no run()")
        return True

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

    # Load repo config
    ctx["repo_config"] = load_repo_config(repo_dir)

    # Load and run test.py
    module = load_test_module(test_dir)
    if module is None:
        print(f"  SKIP: no test.py")
        return True

    if not hasattr(module, "run"):
        print(f"  SKIP: test.py has no run() function")
        return True

    try:
        ctx["result"] = result
        module.run(ctx)
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
    parser.add_argument("range", nargs="?", default=None,
                        help="Test range: 01, 01-12, 01-*, *-07 (default: all)")
    args = parser.parse_args()

    # Load test env and resolve repo_dir from it
    test_env = load_test_env(TESTS_DIR)

    repo_dir = Path(test_env["GIT_REPO_PATH"]).resolve()
    if not (repo_dir / ".git").exists():
        print(f"Error: {repo_dir} is not a git repository", file=sys.stderr)
        sys.exit(1)

    git = GitClient(repo_dir)
    remote_url = git.remote_url()
    print(f"Repo: {repo_dir}")
    print(f"Remote: {remote_url}")
    confirm = input("Continue? [Y/n] ").strip().lower()
    if confirm in ("n", "no"):
        print("Aborted.")
        sys.exit(0)

    tests = discover_tests(TESTS_DIR)
    if not tests:
        print("No test directories found", file=sys.stderr)
        sys.exit(1)

    base_dir = TESTS_DIR / BASE_TEST_DIR_NAME
    if not base_dir.exists():
        print(f"Error: {BASE_TEST_DIR_NAME}/ not found", file=sys.stderr)
        sys.exit(1)

    runner = ZpRunner(repo_dir)

    # Context shared across tests
    ctx = {
        "tests_dir": TESTS_DIR,
        "test_env": test_env,
    }

    # Filter by range
    if args.range:
        tests = filter_tests(tests, args.range)

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
