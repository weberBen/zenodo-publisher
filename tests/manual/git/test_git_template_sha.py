"""Print the current template tag name, commit SHA, and creation date.

Usage:
    python tests/manual/git/test_git_template_sha.py
"""

# RUN :
#
# uv run python tests/manual/git/test_git_template_sha.py
#
#

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.manual.utils import _load_env, get_test_dir
from tests.utils.git import GitClient

_env = _load_env(get_test_dir() / ".zenodo.test.env")
git = GitClient(Path(_env["GIT_REPO_PATH"]))

git._run("fetch", "origin", "--tags", "--force")

tag = git.latest_remote_tag("template_*")
if not tag:
    print("No template_* tag found on remote.", file=sys.stderr)
    sys.exit(1)

sha = git.rev_parse(tag)
date = git.tag_date(tag)

print(f"tag:  {tag}")
print(f"sha:  {sha}")
print(f"date: {date}")
