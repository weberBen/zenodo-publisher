"""Pytest configuration for zenodo-publisher tests.

- Loads .zenodo.test.env at session start (test_env, repo_dir, tests_dir)
- Prompts for confirmation before running (like the old runner)
- Sorts tests by file number (test_00_*, test_01_*, ...)
- Excludes tests/manual/ from collection
- Creates tests/logs/ for output logging
- Provides fixtures: log_dir, repo_dir, repo_git, branch_name
"""

import re
import sys
from pathlib import Path

import pytest
import yaml

from tests.utils.git import GitClient
from tests.utils.github import GithubClient

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
branch_name: str | None = None
git_template_sha: str | None = None
gpg_uid: str | None = None
tests_dir: Path = TESTS_DIR
log_dir: Path = TESTS_DIR / "logs"

# ---------------------------------------------------------------------------
# Collection config
# ---------------------------------------------------------------------------

collect_ignore_glob = ["manual/*"]


def pytest_configure(config):
    config.addinivalue_line("markers", "no_auto_reset: disable auto repo reset after test")


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


def _load_branch_name(repo_path: Path) -> str | None:
    """Read main_branch from zenodo_config.yaml in the repo."""
    config_path = repo_path / "zenodo_config.yaml"
    if not config_path.exists():
        return None
    with open(config_path) as f:
        repo_config = yaml.safe_load(f) or {}
    return repo_config.get("main_branch", "").strip() or None


def pytest_sessionstart(session):
    """Load context and prompt for confirmation before running tests."""
    global test_env, repo_dir, branch_name, git_template_sha, gpg_uid

    test_env.update(_load_test_env(TESTS_DIR))

    repo_dir = Path(test_env["GIT_REPO_PATH"]).resolve()
    if not (repo_dir / ".git").exists():
        print(f"Error: {repo_dir} is not a git repository", file=sys.stderr)
        sys.exit(1)

    git_template_sha = test_env.get("GIT_TEMPLATE_SHA")
    gpg_uid = test_env.get("GPG_UID")
    branch_name = _load_branch_name(repo_dir)

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


# ---------------------------------------------------------------------------
# Repo reset
# ---------------------------------------------------------------------------

def reset_test_repo():
    """Reset the external test repo to its template state."""
    if not repo_dir or not git_template_sha or not branch_name:
        raise RuntimeError(
            "Cannot reset: repo_dir, git_template_sha or branch_name not set"
        )
    git = GitClient(repo_dir)

    # Clean up orphaned GitHub state from failed tests
    gh = GithubClient(repo_dir)

    # Delete draft releases (created when a tag is deleted but release survives)
    # gh release list doesn't show drafts, so we use the API via list_draft_releases
    for draft in gh.list_draft_releases():
        release_id = draft.get("id")
        if release_id:
            gh._run("api", "-X", "DELETE",
                    f"repos/{{owner}}/{{repo}}/releases/{release_id}")

    # Delete remote tags that don't have an associated release (orphans)
    release_tags = {r["tagName"] for r in gh.list_releases()}
    for tag_info in gh.list_tags():
        tag = tag_info["name"]
        if tag not in release_tags:
            gh.delete_tag(tag, dangerous_delete=True)

    git.reset_repo(branch_name, git_template_sha)
    git.add_and_commit()
    git.push()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def fix_log_dir():
    return log_dir


@pytest.fixture(scope="session")
def fix_repo_dir():
    return repo_dir


@pytest.fixture(scope="session")
def fix_repo_git():
    return GitClient(repo_dir)


@pytest.fixture(scope="session")
def fix_branch_name():
    return branch_name


@pytest.fixture(scope="session")
def fix_gpg_uid():
    return gpg_uid


@pytest.fixture
def repo_env(request, fix_repo_dir, fix_repo_git):
    """Yield (repo_dir, git_client), then auto-reset the repo.

    Auto-reset after each test unless opted out:
      - Per test:   @pytest.mark.no_auto_reset
      - Per file:   pytestmark = pytest.mark.no_auto_reset
    """
    yield fix_repo_dir, fix_repo_git

    marker = request.node.get_closest_marker("no_auto_reset")
    if marker is None:
        reset_test_repo()
