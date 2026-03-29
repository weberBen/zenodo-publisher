# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "rfc3161ng>=2.1",
#   "requests>=2.28",
# ]
# ///
"""DigiCert RFC 3161 timestamp module for zenodo-publisher.

Requests a free timestamp from DigiCert TSA (http://timestamp.digicert.com)
for each input file and saves the timestamp response as a .tsr file.

Input (via --input <json_file>):
    {
        "config": {"identity_hash_algo": "sha256"},
        "output_dir": "/tmp/...",
        "files": [
            {
                "file_path": "/tmp/.../paper.pdf",
                "config_key": "paper",
                "hashes": {"sha256": {"value": "abc...", "formatted_value": "sha256:abc..."}},
                "module_config": {"full_chain": true}
            }
        ]
    }

Output (NDJSON on stdout):
    - event lines (detail/detail_ok/warn) relayed by ZP to the user
    - final {"type": "result", "files": [...]} line with produced FileEntry dicts
"""

import argparse
import json
import sys
from pathlib import Path

import requests
import rfc3161ng

TSA_URL = "http://timestamp.digicert.com"


def emit(type_: str, msg: str, **kwargs) -> None:
    """Emit a NDJSON event line to stdout (relayed by ZP to the user)."""
    event = {"type": type_, "msg": msg}
    if kwargs:
        event["data"] = kwargs
    print(json.dumps(event), flush=True)


def timestamp_file(fp: Path, algo: str, full_chain: bool, output_dir: Path) -> Path:
    """Request a RFC 3161 timestamp for fp and save as <fp.name>.tsr."""
    with open(fp, "rb") as fh:
        file_bytes = fh.read()

    # Build timestamp request
    ts_request = rfc3161ng.make_timestamp_request(
        data=file_bytes,
        hash_algorithm=algo,
        include_tsa_certificate=full_chain,
    )

    # POST to DigiCert TSA
    resp = requests.post(
        TSA_URL,
        data=ts_request,
        headers={"Content-Type": "application/timestamp-query"},
        timeout=30,
    )
    resp.raise_for_status()

    tsr_path = output_dir / f"{fp.name}.tsr"
    tsr_path.write_bytes(resp.content)
    return tsr_path


def main() -> None:
    parser = argparse.ArgumentParser(description="DigiCert timestamp module")
    parser.add_argument("--input", required=True, help="Path to input JSON file")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    output_dir = Path(data["output_dir"])
    algo = data["config"].get("identity_hash_algo", "sha256")
    result_files = []

    for file_info in data["files"]:
        fp = Path(file_info["file_path"])
        module_cfg = file_info.get("module_config", {})
        full_chain = module_cfg.get("full_chain", True)

        emit("detail", f"Timestamping '{fp.name}' ({algo})...",
             filename=fp.name, algo=algo, name="digicert_timestamp.start")

        try:
            tsr_path = timestamp_file(fp, algo, full_chain, output_dir)
        except requests.RequestException as e:
            emit("error", f"DigiCert TSA request failed for '{fp.name}': {e}",
                 name="digicert_timestamp.tsa_error")
            sys.exit(1)
        except Exception as e:
            emit("error", f"Timestamping failed for '{fp.name}': {e}",
                 name="digicert_timestamp.error")
            sys.exit(1)

        emit("detail_ok", f"Timestamp saved: {tsr_path.name}",
             tsr=tsr_path.name, name="digicert_timestamp.done")

        result_files.append({
            "file_path": str(tsr_path),
            "config_key": file_info["config_key"],   # same as parent — module_name differentiates
            "module_entry_type": "tsr",
            "publishers": {"destination": {"digicert_timestamp": []}},
        })

    print(json.dumps({"type": "result", "files": result_files}), flush=True)


if __name__ == "__main__":
    main()
