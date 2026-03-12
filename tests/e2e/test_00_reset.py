"""Test: reset the test repo to a known state before running tests."""

from tests import conftest
from tests.utils.git import GitClient
import yaml
from pathlib import Path

def test_reset():
    test_env = conftest.test_env
    repo_dir = test_env["GIT_REPO_PATH"]
    git_template_sha = test_env["GIT_TEMPLATE_SHA"]

    # Load repo config to get main_branch
    config_path = Path(repo_dir) / "zenodo_config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            repo_config = yaml.safe_load(f) or {}
        branch_name = repo_config.get("main_branch", "").strip()
    else:
        branch_name = None
    
    if not branch_name:
        raise Exception("Invalid branch name ")

    git = GitClient(repo_dir)
    git.reset_repo(branch_name, git_template_sha)
    git.add_and_commit()
    git.push()
