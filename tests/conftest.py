"""Pytest configuration for zenodo-publisher tests.

- Loads .zenodo.test.env at session start (test_env, repo_dir, tests_dir)
- Prompts for confirmation before running (like the old runner)
- Sorts tests by file number (test_00_*, test_01_*, ...)
- Excludes tests/manual/ from collection
- Creates tests/logs/ for output logging
"""

import re
import sys
from pathlib import Path

from tests.utils.git import GitClient

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TESTS_DIR = Path(__file__).parent
TEST_ENV_FILENAME = ".zenodo.test.env"

# ---------------------------------------------------------------------------
# Shared context (loaded once at session start, accessible by all tests)
# ---------------------------------------------------------------------------

test_env: dict[str, str] = {}
repo_dir: Path | None = None
tests_dir: Path = TESTS_DIR
log_dir: Path = TESTS_DIR / "logs"

# ---------------------------------------------------------------------------
# Collection config
# ---------------------------------------------------------------------------

collect_ignore_glob = ["manual/*"]


def pytest_collection_modifyitems(items):
    """Sort tests by file number, preserving order within each file."""
    def _sort_key(item):
        filename = Path(item.fspath).stem
        m = re.match(r"test_(\d+)", filename)
        return int(m.group(1)) if m else 9999
    items.sort(key=_sort_key)


# ---------------------------------------------------------------------------
# Session startup
# ---------------------------------------------------------------------------

def _load_test_env(tests_dir: Path) -> dict[str, str]:
    """Load .zenodo.test.env from tests dir."""
    path = tests_dir / TEST_ENV_FILENAME
    if not path.exists():
        print(f"Error: test env file not found: {path}", file=sys.stderr)
        sys.exit(1)
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"')
    return env


def pytest_sessionstart(session):
    """Load context and prompt for confirmation before running tests."""
    global test_env, repo_dir

    test_env.update(_load_test_env(TESTS_DIR))

    repo_dir = Path(test_env["GIT_REPO_PATH"]).resolve()
    if not (repo_dir / ".git").exists():
        print(f"Error: {repo_dir} is not a git repository", file=sys.stderr)
        sys.exit(1)

    git = GitClient(repo_dir)
    remote_url = git.remote_url()
    print(f"\nRepo: {repo_dir}")
    print(f"Remote: {remote_url}")

    if sys.stdin.isatty():
        confirm = input("Continue? [Y/n] ").strip().lower()
        if confirm in ("n", "no"):
            print("Aborted.")
            sys.exit(0)

    log_dir.mkdir(exist_ok=True)
