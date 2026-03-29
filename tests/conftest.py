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
import uuid
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
gpg_uid: str | None = None
session_id: str = uuid.uuid4().hex[:8]
tests_dir: Path = TESTS_DIR
log_dir: Path = TESTS_DIR / "logs"
_session_had_failure: bool = False

# ---------------------------------------------------------------------------
# Collection config
# ---------------------------------------------------------------------------

collect_ignore_glob = ["manual/*"]


def pytest_configure(config):
    config.addinivalue_line("markers", "no_auto_reset: disable auto repo reset after test")
    config.addinivalue_line("markers", "require_all_passed: skip if any previous test failed")


def pytest_runtest_logreport(report):
    """Track any test failure across the session."""
    global _session_had_failure
    if report.failed and report.when == "call":
        _session_had_failure = True


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
    global test_env, repo_dir, branch_name, gpg_uid

    test_env.update(_load_test_env(TESTS_DIR))

    repo_dir = Path(test_env["GIT_REPO_PATH"]).resolve()
    if not (repo_dir / ".git").exists():
        print(f"Error: {repo_dir} is not a git repository", file=sys.stderr)
        sys.exit(1)

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
    if not repo_dir or not branch_name:
        raise RuntimeError(
            "Cannot reset: repo_dir or branch_name not set"
        )
    git = GitClient(repo_dir)

    tag = git.latest_remote_tag("template_*", branch="main")
    if not tag:
        raise RuntimeError("Cannot reset: no remote tag matching 'template_*' found")
    template_sha = git.rev_parse(tag)

    # Clean up orphaned GitHub state from failed tests
    gh = GithubClient(repo_dir)

    # Delete draft releases (created when a tag is deleted but release survives)
    # gh release list doesn't show drafts, so we use the API via list_draft_releases
    for draft in gh.list_draft_releases():
        release_id = draft.get("id")
        if release_id:
            gh._run("api", "-X", "DELETE",
                    f"repos/{{owner}}/{{repo}}/releases/{release_id}")

    # Delete remote tags that don't have an associated release (orphans).
    # Preserve template_* tags — they are infrastructure tags used for repo reset.
    release_tags = {r["tagName"] for r in gh.list_releases()}
    for tag_info in gh.list_tags():
        tag = tag_info["name"]
        if tag.startswith("template_"):
            continue
        if tag not in release_tags:
            gh.delete_tag(tag, dangerous_delete=True)

    # Two-pass reset: the first pass restores template files but git clean -fd
    # does not remove ignored files (e.g. files a test created then gitignored).
    # After template restore, the original .gitignore no longer protects them,
    # so add+commit+push makes them tracked. The second pass then removes them
    # via git rm -rf since they are now tracked.
    git.reset_repo(branch_name, template_sha)
    git.add_and_commit()
    git.push()
    git.reset_repo(branch_name, template_sha)
    git.add_and_commit()
    git.push()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def fix_log_dir():
    return log_dir


@pytest.fixture
def fix_log_path(request):
    """Auto-computed log path from test node ID. One file per test, overwritten each run."""
    node_id = request.node.nodeid
    parts = node_id.split("::")
    file_stem = Path(parts[0]).stem
    func_name = parts[-1].replace("[", "_").replace("]", "").replace("/", "_")
    log_name = f"{file_stem}__{func_name}.log"
    return log_dir / log_name


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


def pytest_runtest_setup(item):
    """Skip tests marked require_all_passed if any previous test failed."""
    if item.get_closest_marker("require_all_passed") and _session_had_failure:
        pytest.skip("Skipping: a previous test failed")
