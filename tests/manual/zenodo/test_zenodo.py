import json
import random
import hashlib
import tempfile
import sys
from pathlib import Path

# ZP root dir
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("urllib3").setLevel(logging.DEBUG)

from tests.manual.utils import (
    ZENODO_API_URL,
    _load_env,
    get_test_dir,
)
from tests.utils.zenodo import ZenodoClient

_env_path = get_test_dir() / ".zenodo.test.env"
_env = _load_env(_env_path)
ZENODO_TOKEN = _env["ZENODO_TOKEN"]
ZENODO_CONCEPT_DOI = _env["ZENODO_CONCEPT_DOI"]
DEFAULT_DIR = str(Path(__file__).resolve().parent) + "/" + "data"

#%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

def dump_json(res, filename="test_zenodo.json", dir=DEFAULT_DIR):
    path = f"{dir}/{filename}"
    with open(path, "w") as f:
        json.dump(res, f, indent=2, ensure_ascii=False)

AUTO_YES = "--yes" in sys.argv

def pause(label: str = ""):
    if AUTO_YES:
        return
    msg = f"\n--- {label} --- " if label else "\n--- "
    input(msg + "Appuyez sur Entrée pour continuer...")

zenodo = ZenodoClient(
    api_url=ZENODO_API_URL,
    token=ZENODO_TOKEN,
)

print("ZENODO_CONCEPT_DOI", ZENODO_CONCEPT_DOI)
print("Data dir", DEFAULT_DIR)

pause("get_latest_version + get_record")

# --- Get latest version ---
res = zenodo.get_latest_version(ZENODO_CONCEPT_DOI)
dump_json(res)
last_record_id = res["id"]
print("last_record_id", last_record_id)

res = zenodo.get_record(last_record_id)
dump_json(res)

pause("get_all_versions")

# --- Get all versions ---
res = zenodo.get_all_versions(ZENODO_CONCEPT_DOI, max=10)
other_record_id = res[random.randint(0, len(res) - 1)]["id"]
dump_json(res, filename=f"zenodo_version.json")

pause("get_other_record")
print("other_record_id", other_record_id)
res = zenodo.get_record(other_record_id)
other_record = res
dump_json(res)

pause("get suer draft + delete draft")

# --- Get user draft ---
res = zenodo.get_user_draft(ZENODO_CONCEPT_DOI)
if res:
    draft_record_id = res["id"]
else:
    draft_record_id = None
    res = {}

print("draft_record_id", draft_record_id)
dump_json(res)

if draft_record_id:
    res = zenodo.delete_draft(draft_record_id)
    print("deleted draft done", res)

pause("get_last_modified_record")

# --- Get last modified record ---
res = zenodo.get_last_modified_record(ZENODO_CONCEPT_DOI)
las_modified_record_id = res["id"]
dump_json(res)

pause("get_file_list")

# --- List files ---
res = zenodo.list_files(las_modified_record_id)
dump_json(res)

file_keys = []
for f in res:
    key = f['key']
    file_id = f['file_id']
    hash = f.get('checksum', 'N/A')

    file_keys.append((key, file_id, hash))

    print(f"({file_id}) {key}: {hash}")

pause("download_single_file")

# --- Download single file ---
key_file = file_keys[random.randint(1, len(file_keys) - 1)]
print("selected file", key_file)
with tempfile.TemporaryDirectory() as tmp:
    dest = zenodo.download_file(las_modified_record_id, key_file[0], Path(tmp))
    print("downloaded to", dest)

    local_md5 = hashlib.md5(dest.read_bytes()).hexdigest()
    print(f"local md5:  md5:{local_md5}")
    print(f"remote md5: {key_file[2]}")
    print(f"match: {f'md5:{local_md5}' == key_file[2]}")

pause("download_all_files")

# --- Download all files ---
with tempfile.TemporaryDirectory() as tmp:
    paths = zenodo.download_all_files(las_modified_record_id, Path(tmp))
    for path in paths:
        local_md5 = f"md5:{hashlib.md5(path.read_bytes()).hexdigest()}"
        remote_md5 = next((h for k, _, h in file_keys if k == path.name), "N/A")
        match = local_md5 == remote_md5
        status = "OK" if match else "MISMATCH"
        print(f"[{status}] {path.name}  local={local_md5}  remote={remote_md5}")

pause("create_draft+delete_draft")

# --- Create draft + delete ---
res = zenodo.create_draft(last_record_id)
draft_record_id = res["id"]
print("draft_record_id", draft_record_id)
dump_json(res)


res = zenodo.delete_draft(draft_record_id)
print("draft deleted", res)

