"""Test: zp archive creates a zip with correct name and hashes."""

from tests.utils.ndjson import find_data, find_errors
from tests.utils.git import GitClient

def run(ctx):
    test_env = ctx["test_env"]
    repo_config = ctx["repo_config"]
    repo_dir = test_env["GIT_REPO_PATH"]
    git_template_sha = test_env["GIT_TEMPLATE_SHA"]
    branch_name = repo_config.main_branch

    git = GitClient(repo_dir)
    git.reset_repo(branch_name, git_template_sha)
    git.add_and_commit()
    git.push()

