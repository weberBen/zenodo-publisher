import hashlib
import json
import random
import tempfile
import sys
from pathlib import Path

# ZP root dir
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("urllib3").setLevel(logging.DEBUG)

from tests.manual.utils import (
    _load_env,
    get_test_dir,
    get_random_string,
)
from tests.utils.git import GitClient

_env_path = get_test_dir() / ".zenodo.test.env"
_env = _load_env(_env_path)
GIT_REPO_PATH = Path(_env["GIT_REPO_PATH"])
DEFAULT_DIR = str(Path(__file__).resolve().parent) + "/" + "data"
TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "template_repo"
MAIN_BRANCH = "main"
ORIGIN_BRANCH = f"origin/{MAIN_BRANCH}"

if not TEMPLATE_DIR.exists():
    raise Exception(f"Template repo dir '{TEMPLATE_DIR}' does not exists")

#%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

def dump_json(res, filename="test_git.json", dir=DEFAULT_DIR):
    path = f"{dir}/{filename}"
    with open(path, "w") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)

AUTO_YES = "--yes" in sys.argv

def pause(label: str = ""):
    if AUTO_YES:
        return
    msg = f"\n--- {label} --- " if label else "\n--- "
    input(msg + "Appuyez sur Entrée pour continuer...")


def add_file(git):
    _id = get_random_string(length=6)
    filename = f"test_{_id}.txt"
    git.add_file(filename, f"content {_id}")
    
    return filename

git = GitClient(GIT_REPO_PATH)

print("Git repo", git.repo_dir)
print("Data dir", DEFAULT_DIR)

git.reset()
git.branch_checkout(MAIN_BRANCH)

# --- Test on existing repo ---

pause("init_repo")

with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp) / "test_repo"
    tmp_git = GitClient.init(tmp_path)
    print(f"Temp repo: {tmp_git.repo_dir}")
    print(f"user.name: {tmp_git.config_get('user.name')}")
    print(f"user.email: {tmp_git.config_get('user.email')}")

pause("add_file")

_id = get_random_string(length=6)
filename = f"test_{_id}.txt"
git.add_file(filename, f"content {_id}")
git.add(filename)
git.commit(f"add test file {_id}")

print(f"HEAD: {git.rev_parse('HEAD')}")
print("diff:", git.diff_names(ORIGIN_BRANCH))

pause("push")
git.push()
print("diff:", git.diff_names(ORIGIN_BRANCH))

pause("add_and_commit")
filename = add_file(git)
git.add_and_commit()
print("diff:", git.diff_names(ORIGIN_BRANCH))

pause("push")
git.push()
print("diff:", git.diff_names(ORIGIN_BRANCH))

pause("reset_repo")
filename = add_file(git)
git.add_and_commit()
print("diff:", git.diff_names(ORIGIN_BRANCH))

_template_tag = git.latest_remote_tag("template_*", branch="main")
git.reset_repo(MAIN_BRANCH, git.rev_parse(_template_tag))
git.commit()
print("diff:", git.diff_names(ORIGIN_BRANCH))
git.push()

