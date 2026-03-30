"""DigiCert RFC 3161 timestamp module for zenodo-publisher.

Requests a free timestamp from DigiCert TSA (http://timestamp.digicert.com)
for each input file and saves the timestamp response as a .tsr file.

Uses the pre-computed hash from the ZP pipeline input — no file I/O needed.

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
    - event lines (detail/detail_ok/warn/error) relayed by ZP to the user
    - final {"type": "result", "files": [...]} line with produced FileEntry dicts
"""

import argparse
import json
import sys
from pathlib import Path

import requests
import rfc3161ng

TSA_URL = "http://timestamp.digicert.com"
SUPPORTED_ALGOS = {"sha1", "sha256", "sha384", "sha512"}


def emit(type_: str, msg: str, **kwargs) -> None:
    """Emit a NDJSON event line to stdout (relayed by ZP to the user)."""
    event = {"type": type_, "msg": msg}
    if kwargs:
        event["data"] = kwargs
    print(json.dumps(event), flush=True)


def request_timestamp(hex_hash: str, algo: str, full_chain: bool,
                      output_dir: Path, filename: str) -> Path:
    """Request a RFC 3161 timestamp for a pre-computed hash and save as <filename>.tsr."""
    digest_bytes = bytes.fromhex(hex_hash)
    ts_request = rfc3161ng.encode_timestamp_request(
        rfc3161ng.make_timestamp_request(
            digest=digest_bytes,
            hashname=algo,
            include_tsa_certificate=full_chain,
        )
    )
    resp = requests.post(
        TSA_URL,
        data=ts_request,
        headers={"Content-Type": "application/timestamp-query"},
        timeout=30,
    )
    resp.raise_for_status()
    tsr_path = output_dir / f"{filename}.tsr"
    tsr_path.write_bytes(resp.content)
    return tsr_path


def check(module_config: dict) -> None:
    """Quick self-check: validate config and verify TSA is reachable."""
    full_chain = module_config.get("full_chain", True)
    if not isinstance(full_chain, bool):
        emit("error", f"Invalid config: 'full_chain' must be a boolean, got {full_chain!r}",
             name="check.invalid_config")
        sys.exit(1)

    emit("detail_ok", "Config valid",
         name="check.ok")


def main() -> None:
    parser = argparse.ArgumentParser(description="DigiCert timestamp module")
    parser.add_argument("--input", help="Path to input JSON file")
    parser.add_argument("--check", action="store_true", help="Run self-check and exit")
    parser.add_argument("--config", help="Path to module config JSON (used with --check)")
    args = parser.parse_args()

    if args.check:
        module_config = {}
        if args.config:
            with open(args.config, encoding="utf-8") as f:
                module_config = json.load(f).get("module_config", {})
        check(module_config)
        return

    if not args.input:
        emit("error", "--input is required when not running --check",
             name="missing_input")
        sys.exit(1)

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    output_dir = Path(data["output_dir"])
    algo = data["config"].get("identity_hash_algo", "sha256")
    if algo not in SUPPORTED_ALGOS:
        emit("error",
             f"identity_hash_algo '{algo}' is not supported by RFC 3161 / DigiCert TSA. "
             f"Use one of: {sorted(SUPPORTED_ALGOS)}",
             name="unsupported_algo")
        sys.exit(1)
    result_files = []

    for file_info in data["files"]:
        filename = Path(file_info["file_path"]).name
        module_cfg = file_info.get("module_config", {})
        full_chain = module_cfg.get("full_chain", True)

        hashes = file_info.get("hashes", {})
        if algo not in hashes:
            emit("error", "Hash '{algo}' not found for '{filename}' "
                 "(available: {available_hash_algo})",
                 algo=algo,
                 filename=filename,
                 available_hash_algo=list(hashes.keys()),
                 name="missing_hash")
            sys.exit(1)
        hex_hash = hashes[algo]["value"]

        emit("detail", f"Timestamping '{filename}' ({algo})...",
             filename=filename, algo=algo, name="start")

        try:
            tsr_path = request_timestamp(hex_hash, algo, full_chain, output_dir, filename)
        except requests.RequestException as e:
            emit("error", f"DigiCert TSA request failed for '{filename}': {e}",
                 name="tsa_error")
            sys.exit(1)
        except Exception as e:
            emit("error", f"Timestamping failed for '{filename}': {e}",
                 name="error")
            sys.exit(1)

        emit("detail_ok", f"Timestamp saved: {tsr_path.name}",
             tsr=tsr_path.name, name="done")

        result_files.append({
            "file_path": str(tsr_path),
            "config_key": file_info["config_key"],
            "module_entry_type": "tsr",
        })

    print(json.dumps({"type": "result", "files": result_files}), flush=True)


if __name__ == "__main__":
    main()
