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
from tests.utils.github import GithubClient

_env_path = get_test_dir() / ".zenodo.test.env"
_env = _load_env(_env_path)
GIT_REPO_PATH = Path(_env["GIT_REPO_PATH"])
DEFAULT_DIR = str(Path(__file__).resolve().parent) + "/" + "data"

#%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

def dump_json(res, filename="test_github.json", dir=DEFAULT_DIR):
    path = f"{dir}/{filename}"
    with open(path, "w") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)

AUTO_YES = "--yes" in sys.argv

def pause(label: str = ""):
    if AUTO_YES:
        return
    msg = f"\n--- {label} --- " if label else "\n--- "
    input(msg + "Appuyez sur Entrée pour continuer...")

github = GithubClient(GIT_REPO_PATH)

print("Git repo", GIT_REPO_PATH)
print("Data dir", DEFAULT_DIR)

pause("get_list_release")

res = github.list_releases()
dump_json(res)

selected_release = res[random.randint(0, min(len(res)-1, 10))]
selected_tag = selected_release["tagName"]
print("selected_release", selected_release)
print("release url", github.get_release_url(selected_tag))

pause("get_list_tag")

res = github.list_tags()
dump_json(res)

pause("get_list_release_assets")

res = github.list_release_assets(selected_tag)
dump_json(res)
selected_asset = None
if res:
    selected_asset = res[random.randint(0, len(res)-1)]
    print("selected_asset", selected_asset["name"])
else:
    print(f"No assets for {selected_tag}")

pause("upload_asset")

with tempfile.TemporaryDirectory() as tmp:
    _id = get_random_string()
    filename = f"auto_test_file_{_id}.txt"
    tmp_file = Path(tmp) / filename
    content = f"hello {_id}"
    tmp_file.write_text(content)
    local_sha256 = hashlib.sha256(content.encode()).hexdigest()
    print(f"Uploading {filename} to {selected_tag} release")
    github.upload_asset(selected_tag, tmp_file)

pause("get_release_asset")

res = github.get_release_asset(selected_tag, filename)
dump_json(res)
remote_digest = res.get("digest", "") if res else ""
remote_sha256 = remote_digest.removeprefix("sha256:")
match = local_sha256 == remote_sha256
print(f"local:  sha256:{local_sha256}")
print(f"remote: {remote_digest}")
print(f"match: {match}")

pause("download_asset")

dl_name = selected_asset["name"] if selected_asset else filename
dl_asset_info = github.get_release_asset(selected_tag, dl_name)
with tempfile.TemporaryDirectory() as dl_dir:
    dl_path = github.download_asset(selected_tag, dl_name, Path(dl_dir))
    dl_sha256 = hashlib.sha256(dl_path.read_bytes()).hexdigest()
    remote_digest_dl = dl_asset_info.get("digest", "") if dl_asset_info else ""
    remote_sha256_dl = remote_digest_dl.removeprefix("sha256:")
    status = (dl_sha256 == remote_sha256_dl) and (dl_path.name == dl_asset_info["name"])
    print(f"download: local={dl_path.name} remote={dl_asset_info['name']}")
    print(f"  local:  sha256:{dl_sha256}")
    print(f"  remote: {remote_digest_dl}")
    print(f"  match:  {status}")

pause("download_all_assets")

with tempfile.TemporaryDirectory() as dl_all_dir:
    all_files = github.download_all_assets(selected_tag, Path(dl_all_dir))
    all_assets = github.list_release_assets(selected_tag)
    all_status = True
    for f in all_files:
        local_h = hashlib.sha256(f.read_bytes()).hexdigest()
        asset_info = next((a for a in all_assets if a.get("name") == f.name), None)
        remote_d = asset_info.get("digest", "") if asset_info else ""
        remote_h = remote_d.removeprefix("sha256:")
        remote_name = asset_info.get("name", "?") if asset_info else "?"
        status = (local_h == remote_h) and (f.name == remote_name)
        all_status = all_status and status
        status = "OK" if status else "MISMATCH"
        print(f"  local={f.name} remote={remote_name}: {status} (sha256:{local_h})")
    print("OK" if all_status else "MISMATCH")


pause("create_release")

new_tag_name = "v_" + get_random_string(length=6)
res = github.create_release(new_tag_name, "Ne rele", "body test")
print("Release created", res)
print("New release url", github.get_release_url(new_tag_name))

pause("edit_release_title")

github.edit_release(new_tag_name, title="New title")
print(f"edited title of {new_tag_name}")

pause("edit_release_body")

github.edit_release(new_tag_name, body="New body content")
print(f"edited body of {new_tag_name}")

pause("edit_release_tag")

new_new_tag_name = "v_" + get_random_string(length=6)
github.edit_release(new_tag_name, new_tag=new_new_tag_name)
print(f"moved release from {new_tag_name} to {new_new_tag_name}")
print("New release url", github.get_release_url(new_new_tag_name))

pause("test delete_tag_without_release")
deleted = False
try:
    res = github.delete_tag(new_new_tag_name)
    deleted = True
except ValueError as e:
    pass

if deleted:
    raise Exception("Deleted tag that has release associated to it")
else:
    print("Test ok (release exists, cannot delete tag only without release)")

pause("rever_edit_release_tag")
github.edit_release(new_new_tag_name, new_tag=new_tag_name)

pause("delete_trash_releases")
github.delete_release(new_tag_name, cleanup_tag=True)
print(f"deleted release {new_tag_name} (with tag cleanup)")

github.delete_release(new_new_tag_name, cleanup_tag=False)
print(f"deleted release {new_new_tag_name} (with tag cleanup)")

pause("delete_tag")
res = github.delete_tag(new_new_tag_name)
dump_json(res)

pause("create_release_for_tag_info")

tag_info_name = "v_" + get_random_string(length=6)
github.create_release(tag_info_name, "Tag info test", "body tag info")
print(f"Release created: {tag_info_name}")

pause("get_tag_info")

tag_info = github.get_tag_info(tag_info_name)
dump_json(tag_info)

pause("get_release_info")

release_info = github.get_release_info(tag_info_name)
dump_json(release_info)

tag_commit = tag_info["commit_sha"]
print(f"Tag commit_sha: {tag_commit}")

pause("delete_tag_to_create_draft")
# create a draft by deleting tag associated to release withtout deletin release
github.delete_tag(tag_info_name, dangerous_delete=True)
print(f"Deleted tag {tag_info_name}")

pause("get_latest_release")

latest = github.get_latest_release()
dump_json(latest)
print(f"Latest release: {latest['tag_name']} — {latest['name']}")

if latest["draft"] == True:
    raise Exception("Latest should no be draft")

pause("list_draft_releases")

drafts = github.list_draft_releases()
print(f"Found {len(drafts)} draft(s):")
for d in drafts:
    print(f"  {d['tagName']} — {d['name']} (draft={d['isDraft']})")

if not any(d["tagName"] == tag_info_name for d in drafts):
    raise Exception(f"Expected draft release with tagName={tag_info_name} not found")

dump_json(drafts)

pause("cleanup_drafts")

for d in drafts:
    github.delete_release(d["tagName"], cleanup_tag=True)
    print(f"Deleted draft release {d['tagName']}")
print("Cleanup done")
