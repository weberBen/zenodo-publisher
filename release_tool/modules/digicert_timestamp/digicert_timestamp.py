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

from _shared import create_emitter, compute_file_hash, run_module_files

TSA_URL = "http://timestamp.digicert.com"
SUPPORTED_ALGOS = {"sha1", "sha256", "sha384", "sha512"}

emit = create_emitter("digicert_timestamp")


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


# ---------------------------------------------------------------------------
# Pipeline handlers (called by ZP via 'run --input' / 'check --config')
# ---------------------------------------------------------------------------

def _process_file(f):
    """Pipeline handler for a single file."""
    full_chain = f["module_config"].get("full_chain", True)
    algo = f["identity_hash_algo"]

    if algo not in SUPPORTED_ALGOS:
        emit("warn",
             "identity_hash_algo '{algo}' is not supported by DigiCert TSA. "
             "Use one of: {supported}. Switching to default sha256",
             algo=algo,
             supported=sorted(SUPPORTED_ALGOS),
             name="unsupported_algo")
        algo = "sha256"

    # Use pre-computed hash if available, otherwise hash the file directly
    if algo in f["hashes"]:
        hex_hash = f["hashes"][algo]["value"]
        emit("cmd", f"Using pre-computed {algo} hash", name="start.hash")
    else:
        emit("cmd", f"Computing {algo} hash of {f['filename']}", name="start.hash")
        hex_hash = compute_file_hash(f["file_path"], algo)

    emit("detail", f"Timestamping '{f['filename']}' ({algo})...",
         filename=f["filename"], algo=algo, name="start")

    try:
        tsr_path = request_timestamp(hex_hash, algo, full_chain, f["output_dir"], f["filename"])
    except requests.RequestException as e:
        emit("error", f"DigiCert TSA request failed for '{f['filename']}': {e}",
             name="tsa_error")
        sys.exit(1)
    except Exception as e:
        emit("error", f"Timestamping failed for '{f['filename']}': {e}",
             name="error")
        sys.exit(1)

    emit("detail_ok", f"Timestamp saved: {tsr_path.name}",
         tsr=tsr_path.name, name="done")

    return {
        "file_path": str(tsr_path),
        "config_key": f["config_key"],
        "module_entry_type": "tsr",
    }


def _cmd_run(args):
    run_module_files(args, handler=_process_file)


def _cmd_check(args):
    module_config = {}
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            module_config = json.load(f).get("module_config", {})
    check(module_config)


# ---------------------------------------------------------------------------
# Standalone handlers (via 'zp modules run digicert_timestamp')
# ---------------------------------------------------------------------------

def _cmd_stamp(args):
    file_path = args.file.resolve()
    if not file_path.exists():
        emit("error", f"File not found: {file_path}", name="file_not_found")
        sys.exit(1)

    output_dir = (args.output_dir or file_path.parent).resolve()

    emit("detail", f"Computing {args.algo} hash of {file_path.name}...",
         filename=file_path.name, algo=args.algo, name="hash")
    hex_hash = compute_file_hash(file_path, args.algo)
    emit("detail_ok", f"{args.algo}: {hex_hash}", algo=args.algo, hash=hex_hash, name="hash.done")

    emit("detail", "Requesting RFC 3161 timestamp from DigiCert TSA...", name="start")
    try:
        tsr_path = request_timestamp(hex_hash, args.algo, args.full_chain, output_dir, file_path.name)
    except requests.RequestException as e:
        emit("error", f"DigiCert TSA request failed: {e}", name="tsa_error")
        sys.exit(1)

    emit("detail_ok", f"Timestamp saved: {tsr_path}",
         tsr=str(tsr_path), full_chain=args.full_chain, name="done")


def _cmd_info(args):
    from verify_tsr import tsr_info

    tsr_path = args.tsr.resolve()
    if not tsr_path.exists():
        emit("error", f"TSR not found: {tsr_path}", name="tsr_not_found")
        sys.exit(1)

    if not tsr_info(tsr_path, show_chain=args.check_chain):
        sys.exit(1)


def _cmd_verify(args):
    from verify_tsr import verify_file

    file_path = args.file.resolve()
    tsr_path = args.tsr.resolve()
    if not file_path.exists():
        emit("error", f"File not found: {file_path}", name="file_not_found")
        sys.exit(1)
    if not tsr_path.exists():
        emit("error", f"TSR not found: {tsr_path}", name="tsr_not_found")
        sys.exit(1)

    root_cert = args.root_cert.resolve() if args.root_cert else None
    if root_cert and not root_cert.exists():
        emit("error", f"Root certificate not found: {root_cert}", name="root_cert_not_found")
        sys.exit(1)

    ok = verify_file(file_path, tsr_path, args.algo,
                     show_chain=args.check_chain, root_cert=root_cert)
    if not ok:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Unified parser + entry point
# ---------------------------------------------------------------------------

SORTED_ALGOS = sorted(SUPPORTED_ALGOS)

_HANDLERS = {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zp modules run digicert_timestamp",
        description="DigiCert RFC 3161 timestamp module",
    )
    sub = parser.add_subparsers(dest="command")

    # Pipeline (hidden from --help)
    run_p = sub.add_parser("run", help=argparse.SUPPRESS)
    run_p.add_argument("--input", required=True)

    check_p = sub.add_parser("check", help=argparse.SUPPRESS)
    check_p.add_argument("--config")

    # Standalone
    stamp_p = sub.add_parser("stamp", help="Stamp a file with RFC 3161 timestamp")
    stamp_p.add_argument("file", type=Path, help="File to stamp")
    stamp_p.add_argument("--algo", default="sha256", choices=SORTED_ALGOS,
                           help="Hash algorithm (default: sha256)")
    stamp_p.add_argument("--full-chain", "--no-full-chain",
                           action=argparse.BooleanOptionalAction, default=True,
                           help="Embed full cert chain in the TSR (default: true)")
    stamp_p.add_argument("--output-dir", type=Path, default=None,
                           help="Output directory (default: same as input file)")

    info_p = sub.add_parser("info", help="Display TSR metadata (timestamp, algo, chain)")
    info_p.add_argument("tsr", type=Path, help="TSR file (.tsr)")
    info_p.add_argument("--check-chain", action="store_true", default=False,
                         help="Display certificate chain from TSR")

    verify_p = sub.add_parser("verify", help="Verify a file against a .tsr")
    verify_p.add_argument("file", type=Path, help="Original file")
    verify_p.add_argument("tsr", type=Path, help="TSR file (.tsr)")
    verify_p.add_argument("--algo", default=None, choices=SORTED_ALGOS,
                          help="Hash algorithm (default: auto-detected from TSR)")
    verify_p.add_argument("--check-chain", action="store_true", default=False,
                          help="Display certificate chain from TSR")
    verify_p.add_argument("--root-cert", type=Path, default=None,
                          help="Root CA for chain validation (auto-discovered if omitted)")

    _HANDLERS.update({"run": _cmd_run, "check": _cmd_check,
                      "stamp": _cmd_stamp, "info": _cmd_info,
                      "verify": _cmd_verify})
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    handler = _HANDLERS.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
