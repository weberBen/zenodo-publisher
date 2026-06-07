"""OpenTimestamps module for zenodo-publisher.

Anchors file hashes in the Bitcoin blockchain via the OpenTimestamps protocol.
Unlike DigiCert (centralized, instant), OTS is decentralized and trustless
but the full proof takes hours (waiting for Bitcoin block confirmation).

Pipeline mode (run): stamps files immediately, produces .ots (pending proof).
Standalone: stamp, upgrade, verify, info subcommands.

Input (via --input <json_file>):
    Same format as digicert_timestamp — see ZP module protocol.

Output (NDJSON on stdout):
    - event lines relayed by ZP
    - final {"type": "result", "files": [...]} with produced .ots files
"""

import argparse
import json
import logging
import os
import sys
from binascii import hexlify
from pathlib import Path

import requests
from _shared import create_emitter, compute_file_hash, run_module_files, run_module_job_files

from opentimestamps.core.notary import (
    BitcoinBlockHeaderAttestation, PendingAttestation,
)
from opentimestamps.core.op import (
    OpAppend, OpSHA1, OpSHA256, OpRIPEMD160, OpKECCAK256,
)
from opentimestamps.core.serialize import (
    StreamSerializationContext, StreamDeserializationContext,
)
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp
from otsclient.cmds import create_timestamp, upgrade_timestamp

DEFAULT_CALENDAR_URLS = [
    "https://alice.btc.calendar.opentimestamps.org",  # Alice
    "https://bob.btc.calendar.opentimestamps.org",  # Bob
    "https://finney.calendar.eternitywall.com",    # Finney (EU)
    "https://ots.btc.catallaxy.com",      # Catallaxy (Canada)
]

SUPPORTED_ALGOS = {
    "sha1": OpSHA1,
    "sha256": OpSHA256,
    "ripemd160": OpRIPEMD160,
    "keccak256": OpKECCAK256,
}


emit = create_emitter("ots_timestamp")


# ---------------------------------------------------------------------------
# Redirect OTS client logging to emit events
# ---------------------------------------------------------------------------

class _EmitLogHandler(logging.Handler):
    """Redirect Python logging from OTS client to NDJSON emit events."""

    _LEVEL_MAP = {
        logging.DEBUG: "debug",
        logging.INFO: "detail",
        logging.WARNING: "warn",
        logging.ERROR: "error",
        logging.CRITICAL: "error",
    }

    _DEMOTE_TO_DEBUG = {"Not found", "Pending confirmation"}

    def emit(self, record):
        event_type = self._LEVEL_MAP.get(record.levelno, "debug")
        msg = record.getMessage()
        if event_type == "warn" and any(s in msg for s in self._DEMOTE_TO_DEBUG):
            event_type = "debug"
        emit(event_type, msg, name="client.log")


def _setup_logging():
    debug = os.environ.get("ZP_DEBUG") == "true"
    handler = _EmitLogHandler()
    handler.setLevel(logging.DEBUG if debug else logging.WARNING)
    # Replace all handlers on root logger to capture OTS client output
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if debug else logging.WARNING)


_setup_logging()


# ---------------------------------------------------------------------------
# Shim: make create_timestamp happy (it expects args.m, args.timeout, etc.)
# ---------------------------------------------------------------------------

class _NoOpCache:
    """Dummy cache that stores nothing. Satisfies the TimestampCache interface."""
    def __getitem__(self, key):
        raise KeyError(key)
    def __contains__(self, key):
        return False
    def merge(self, timestamp):
        pass


class _StampArgs:
    """Minimal args object compatible with otsclient.cmds.create_timestamp/upgrade_timestamp."""
    def __init__(self, calendar_urls, timeout=15, m=None):
        self.calendar_urls = calendar_urls
        self.timeout = timeout
        self.m = m if m is not None else max(1, len(calendar_urls) // 2)
        # create_timestamp
        self.use_btc_wallet = False
        self.setup_bitcoin = False
        # upgrade_timestamp
        self.cache = _NoOpCache()
        self.whitelist = set()
        self.dry_run = False
        self.wait = False
        self.wait_interval = 30


BLOCKSTREAM_API = "https://blockstream.info/api"


# ---------------------------------------------------------------------------
# Bitcoin block header
# ---------------------------------------------------------------------------

def fetch_block_header(height: int) -> dict | None:
    """Fetch a Bitcoin block header from Blockstream.info API."""
    try:
        r = requests.get(f"{BLOCKSTREAM_API}/block-height/{height}", timeout=30)
        r.raise_for_status()
        block_hash = r.text.strip()

        r = requests.get(f"{BLOCKSTREAM_API}/block/{block_hash}", timeout=30)
        r.raise_for_status()
        info = r.json()

        r = requests.get(f"{BLOCKSTREAM_API}/block/{block_hash}/header", timeout=30)
        r.raise_for_status()
        raw_header_hex = r.text.strip()

        return {
            "hash": block_hash,
            "height": info["height"],
            "merkle_root": info["merkle_root"],
            "timestamp": info["timestamp"],
            "raw_header": raw_header_hex,
            "previousblockhash": info.get("previousblockhash", ""),
            "nonce": info["nonce"],
            "bits": info["bits"],
        }
    except Exception as e:
        emit("warn", "Could not fetch block {height}: {error}",
             height=height, error=str(e), name="block.fetch_error")
        return None


def verify_block_attestation(msg: bytes, att: BitcoinBlockHeaderAttestation) -> dict | None:
    """Verify a Bitcoin attestation against Blockstream API.

    Returns the block header dict if verified, None if API unreachable.
    Raises ValueError if merkle root doesn't match.
    """
    height = att.height
    ots_root = hexlify(msg[::-1]).decode()

    header = fetch_block_header(height)
    if header is None:
        return None

    if ots_root != header["merkle_root"]:
        raise ValueError(
            f"Block {height}: merkle root mismatch "
            f"(OTS: {ots_root}, block: {header['merkle_root']})"
        )

    return header


# ---------------------------------------------------------------------------
# Core OTS operations
# ---------------------------------------------------------------------------

def _serialize_ots(file_timestamp, output_path: Path) -> Path:
    with open(output_path, "wb") as fd:
        ctx = StreamSerializationContext(fd)
        file_timestamp.serialize(ctx)
    return output_path


def _deserialize_ots(ots_path: Path) -> DetachedTimestampFile:
    with open(ots_path, "rb") as fd:
        ctx = StreamDeserializationContext(fd)
        return DetachedTimestampFile.deserialize(ctx)


def _add_nonce(timestamp: Timestamp) -> Timestamp:
    """Add a privacy nonce so calendar servers never see the real file hash."""
    nonce_ts = timestamp.ops.add(OpAppend(os.urandom(16)))
    return nonce_ts.ops.add(OpSHA256())


def stamp_file(file_path: Path, output_dir: Path,
               calendar_urls: list[str] | None = None,
               hash_algo: str = "sha256",
               timeout: int = 15,
               nonce: bool = True) -> Path:
    """Stamp a file via OpenTimestamps calendar servers.

    Returns path to the .ots file (pending proof).
    """
    if hash_algo not in SUPPORTED_ALGOS:
        raise ValueError(f"Unsupported algo '{hash_algo}'. Use: {list(SUPPORTED_ALGOS)}")

    calendars = calendar_urls or DEFAULT_CALENDAR_URLS
    op_cls = SUPPORTED_ALGOS[hash_algo]

    with open(file_path, "rb") as fd:
        file_timestamp = DetachedTimestampFile.from_fd(op_cls(), fd)

    merkle_tip = _add_nonce(file_timestamp.timestamp) if nonce else file_timestamp.timestamp

    args = _StampArgs(calendars, timeout=timeout)
    create_timestamp(merkle_tip, calendars, args)

    return _serialize_ots(file_timestamp, output_dir / f"{file_path.name}.ots")


def stamp_hash(hex_hash: str, hash_algo: str, filename: str, output_dir: Path,
               calendar_urls: list[str] | None = None,
               timeout: int = 15,
               nonce: bool = True) -> Path:
    """Stamp a pre-computed hash via OpenTimestamps calendar servers.

    Returns path to the .ots file (pending proof).
    """
    if hash_algo not in SUPPORTED_ALGOS:
        raise ValueError(f"Unsupported algo '{hash_algo}'. Use: {list(SUPPORTED_ALGOS)}")

    calendars = calendar_urls or DEFAULT_CALENDAR_URLS
    op_cls = SUPPORTED_ALGOS[hash_algo]
    digest_bytes = bytes.fromhex(hex_hash)
    file_timestamp = DetachedTimestampFile(op_cls(), Timestamp(digest_bytes))

    merkle_tip = _add_nonce(file_timestamp.timestamp) if nonce else file_timestamp.timestamp

    args = _StampArgs(calendars, timeout=timeout)
    create_timestamp(merkle_tip, calendars, args)

    return _serialize_ots(file_timestamp, output_dir / f"{filename}.ots")


def upgrade_ots(ots_path: Path, calendar_urls: list[str] | None = None) -> bool:
    """Try to upgrade a pending .ots proof.

    Returns True if the proof was upgraded (new attestations found).
    """
    calendars = calendar_urls or DEFAULT_CALENDAR_URLS
    detached = _deserialize_ots(ots_path)

    if _is_complete(detached.timestamp):
        return False

    args = _StampArgs(calendars)
    changed = upgrade_timestamp(detached.timestamp, args)

    if changed:
        # Backup before overwriting
        bak = Path(str(ots_path) + ".bak")
        if bak.exists():
            bak.unlink()
        ots_path.rename(bak)
        _serialize_ots(detached, ots_path)

    return changed


def _is_complete(timestamp) -> bool:
    """Check if a timestamp has any Bitcoin attestation (uses built-in traversal)."""
    return any(
        isinstance(att, BitcoinBlockHeaderAttestation)
        for _, att in timestamp.all_attestations()
    )


def is_ots_complete(ots_path: Path) -> bool:
    detached = _deserialize_ots(ots_path)
    return _is_complete(detached.timestamp)


def _count_chains(timestamp) -> list[dict]:
    """Walk timestamp tree, return summary of each chain (op count + attestation)."""
    chains = []

    def _walk(ts, depth):
        for att in ts.attestations:
            if isinstance(att, BitcoinBlockHeaderAttestation):
                chains.append({"ops": depth, "type": "bitcoin", "block_height": att.height})
            elif isinstance(att, PendingAttestation):
                uri = att.uri.decode() if isinstance(att.uri, bytes) else str(att.uri)
                chains.append({"ops": depth, "type": "pending", "calendar": uri})
        for _, next_ts in ts.ops.items():
            _walk(next_ts, depth + 1)

    _walk(timestamp, 0)
    return chains


def get_ots_info(ots_path: Path) -> dict:
    """Extract metadata from an .ots file."""
    detached = _deserialize_ots(ots_path)

    algo = getattr(detached.file_hash_op, "TAG_NAME", type(detached.file_hash_op).__name__)

    attestations = []
    for msg, att in detached.timestamp.all_attestations():
        if isinstance(att, BitcoinBlockHeaderAttestation):
            attestations.append({
                "type": "bitcoin",
                "block_height": att.height,
            })
        elif isinstance(att, PendingAttestation):
            uri = att.uri.decode() if isinstance(att.uri, bytes) else str(att.uri)
            attestations.append({
                "type": "pending",
                "calendar": uri,
            })

    return {
        "file_hash": hexlify(detached.file_digest).decode(),
        "hash_algo": algo,
        "attestations": attestations,
        "complete": any(a["type"] == "bitcoin" for a in attestations),
        "chains": _count_chains(detached.timestamp),
    }


# ---------------------------------------------------------------------------
# Pipeline handlers (called by ZP via 'run --input' / 'check --config')
# ---------------------------------------------------------------------------

def _process_file(f):
    """Pipeline handler for a single file."""
    cfg = f["module_config"]
    calendar_urls = cfg.get("calendars", cfg.get("calendar_urls", None))
    nonce = cfg.get("nonce", True)
    upgrade_cfg = cfg.get("upgrade", {})
    algo = f["identity_hash_algo"]

    if algo not in SUPPORTED_ALGOS:
        emit("warn",
             "identity_hash_algo '{algo}' is not supported by OTS. "
             "Use one of: {supported}. Switching to default sha256",
             algo=algo,
             supported=sorted(SUPPORTED_ALGOS.keys()),
             name="unsupported_algo")
        algo = "sha256"

    emit("detail", "Stamping '{filename}'...",
         filename=f["filename"], name="start")

    try:
        if algo in f["hashes"]:
            hex_hash = f["hashes"][algo]["value"]
            emit("cmd", "Using pre-computed {algo} hash", algo=algo, name="start.hash")
            ots_path = stamp_hash(hex_hash, algo, f["filename"], f["output_dir"],
                                  calendar_urls=calendar_urls, nonce=nonce)
        else:
            ots_path = stamp_file(f["file_path"], f["output_dir"],
                                  calendar_urls=calendar_urls, hash_algo=algo, nonce=nonce)
    except Exception as e:
        emit("error", "Stamping failed for '{filename}': {error}",
             filename=f["filename"], error=str(e), name="error")
        sys.exit(1)

    emit("detail_ok", "Timestamp saved: {ots}",
         ots=ots_path.name, name="done")

    return {
        "file_path": str(ots_path),
        "config_key": f["config_key"],
        "module_entry_type": "ots",
        "module_config": {
            "calendars": calendar_urls or DEFAULT_CALENDAR_URLS,
            "nonce": nonce,
            "upgrade": upgrade_cfg,
        },
    }


def _cmd_run(args):
    # Read config to determine retry interval from upgrade config
    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    sample_cfg = {}
    if data.get("files"):
        sample_cfg = data["files"][0].get("module_config", {})
    upgrade_cfg = sample_cfg.get("upgrade", {})
    retry_interval = upgrade_cfg.get("retry_interval", "1h")

    job_descriptor = {
        "description": "Upgrade pending OTS proofs to Bitcoin attestation",
        "retry_interval": retry_interval,
        "retry_max": None,
    }
    run_module_files(args, handler=_process_file, result_extra={"job": job_descriptor})


def _cmd_check(args):
    """Check that at least one calendar server is reachable."""
    module_config = {}
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            module_config = json.load(f).get("module_config", {})

    calendar_urls = module_config.get("calendars", module_config.get("calendar_urls", DEFAULT_CALENDAR_URLS))

    reachable = 0
    for url in calendar_urls:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code < 500:
                reachable += 1
                emit("detail_ok", f"Calendar {url}: reachable", name="check.calendar.ok")
            else:
                emit("warn", f"Calendar {url}: HTTP {r.status_code}", name="check.calendar.warn")
        except Exception as e:
            emit("warn", f"Calendar {url}: {e}", name="check.calendar.warn")

    if reachable == 0:
        emit("error", "No calendar servers reachable", name="check.failed")
        sys.exit(1)

    emit("detail_ok", f"Config valid ({reachable}/{len(calendar_urls)} calendars reachable)",
         name="check.ok")


# ---------------------------------------------------------------------------
# Job handler (called by ZP via 'job --input')
# ---------------------------------------------------------------------------

def _process_job_file(f):
    """Job handler: upgrade a pending .ots proof."""
    cfg = f["module_config"]
    calendar_urls = cfg.get("calendars", None)

    ots_path = f["file_path"]
    if not ots_path.exists():
        emit("warn", "OTS file not found: {path}", path=str(ots_path), name="job.missing")
        return {"status": "error"}

    if is_ots_complete(ots_path):
        emit("detail_ok", "Already complete: {filename}", filename=ots_path.name, name="job.already_complete")
        return {"status": "complete"}

    emit("detail", "Upgrading '{filename}'...", filename=ots_path.name, name="job.upgrade.start")

    try:
        changed = upgrade_ots(ots_path, calendar_urls=calendar_urls)
    except Exception as e:
        emit("error", "Upgrade failed: {error}", error=str(e), name="job.upgrade.error")
        return {"status": "error"}

    if changed and is_ots_complete(ots_path):
        emit("detail_ok", "Upgraded to Bitcoin attestation: {filename}",
             filename=ots_path.name, name="job.upgrade.complete")

        # Save block header if configured
        save_header = cfg.get("upgrade", {}).get("save_header", False)
        if save_header:
            _save_block_headers(ots_path)

        return {"status": "complete"}

    if changed:
        emit("detail", "Partially upgraded (still pending): {filename}",
             filename=ots_path.name, name="job.upgrade.partial")
    else:
        emit("detail", "No new attestations yet: {filename}",
             filename=ots_path.name, name="job.upgrade.pending")

    return {"status": "pending"}


def _save_block_headers(ots_path: Path):
    """Save verified block headers for a complete .ots file."""
    detached = _deserialize_ots(ots_path)
    verified_headers = []
    seen_blocks = set()

    for msg, att in detached.timestamp.all_attestations():
        if isinstance(att, BitcoinBlockHeaderAttestation) and att.height not in seen_blocks:
            seen_blocks.add(att.height)
            try:
                header = verify_block_attestation(msg, att)
                if header:
                    verified_headers.append(header)
            except ValueError:
                pass

    if verified_headers:
        header_path = Path(str(ots_path).removesuffix(".ots") + ".blockheader.json")
        existing = {"file_digest": hexlify(detached.file_digest).decode(), "blocks": []}
        if header_path.exists():
            with open(header_path) as fp:
                existing = json.load(fp)
        known_heights = {b["height"] for b in existing["blocks"]}
        for h in verified_headers:
            if h["height"] not in known_heights:
                existing["blocks"].append(h)
        with open(header_path, "w") as fp:
            json.dump(existing, fp, indent=2)
        emit("detail_ok", "Block headers saved: {path}", path=str(header_path), name="job.header.saved")


def _cmd_job(args):
    run_module_job_files(args, handler=_process_job_file)


# ---------------------------------------------------------------------------
# Standalone handlers (via 'zp modules run ots_timestamp')
# ---------------------------------------------------------------------------

def _cmd_stamp(args):
    for file_path in args.files:
        file_path = file_path.resolve()
        if not file_path.exists():
            emit("error", f"File not found: {file_path}", name="file_not_found")
            sys.exit(1)

        output_dir = (args.output_dir or file_path.parent).resolve()

        emit("detail", f"Stamping '{file_path.name}'...", name="stamp.start")

        try:
            ots_path = stamp_file(file_path, output_dir,
                                  calendar_urls=args.calendar_urls or None,
                                  hash_algo=args.algo,
                                  nonce=not args.no_nonce)
        except Exception as e:
            emit("error", f"Stamping failed: {e}", name="stamp.error")
            sys.exit(1)

        emit("detail_ok", f"Timestamp saved: {ots_path}",
             ots=str(ots_path), name="stamp.done")


def _human_timedelta(seconds: float) -> str:
    """Format seconds as human-readable time delta (e.g. '4 hours ago')."""
    mins = int(seconds // 60)
    hours = int(seconds // 3600)
    days = int(seconds // 86400)
    if days > 0:
        return f"{days} day{'s' if days > 1 else ''} ago"
    if hours > 0:
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    if mins > 0:
        return f"{mins} minute{'s' if mins > 1 else ''} ago"
    return "just now"


def _cmd_upgrade(args):
    import time as _time

    for ots_path in args.files:
        ots_path = ots_path.resolve()
        if not ots_path.exists():
            emit("error", f"OTS file not found: {ots_path}", name="upgrade.not_found")
            sys.exit(1)

        emit("detail", f"Upgrading '{ots_path.name}'...", name="upgrade.start")

        already_complete = is_ots_complete(ots_path)

        if not already_complete:
            try:
                changed = upgrade_ots(ots_path, calendar_urls=args.calendar_urls or None)
            except Exception as e:
                emit("error", f"Upgrade failed: {e}", name="upgrade.error")
                sys.exit(1)

            if changed and is_ots_complete(ots_path):
                emit("step_ok", f"Upgraded to Bitcoin attestation: {ots_path.name}",
                     name="upgrade.complete")
            elif changed:
                emit("detail_ok", f"Partially upgraded (still pending): {ots_path.name}",
                     name="upgrade.partial")
                continue
            else:
                emit("warn", f"No new attestations available yet: {ots_path.name}",
                     name="upgrade.pending")
                continue

        # Collect attestations
        detached = _deserialize_ots(ots_path)
        now = _time.time()
        verified_headers = []
        bitcoin_atts = []
        pending_uris = []
        seen_blocks = set()

        for msg, att in detached.timestamp.all_attestations():
            if isinstance(att, BitcoinBlockHeaderAttestation):
                if att.height not in seen_blocks:
                    seen_blocks.add(att.height)
                    bitcoin_atts.append((msg, att))
            elif isinstance(att, PendingAttestation):
                uri = att.uri if isinstance(att.uri, str) else att.uri.decode()
                if uri not in pending_uris:
                    pending_uris.append(uri)

        # Verify confirmed attestations
        for msg, att in bitcoin_atts:
            try:
                header = verify_block_attestation(msg, att)
            except ValueError as e:
                emit("error", str(e), name="upgrade.verify.mismatch")
                sys.exit(1)

            if header:
                verified_headers.append(header)
                block_time = _time.strftime("%Y-%m-%d %H:%M:%S UTC",
                                            _time.gmtime(header["timestamp"]))
                ago = _human_timedelta(now - header["timestamp"])
                emit("detail_ok", "Block {height}: {time} ({ago})",
                     height=att.height, time=block_time, ago=ago,
                     block_hash=header["hash"], name="upgrade.block.ok")
            else:
                emit("warn", "Block {height}: API unreachable",
                     height=att.height, name="upgrade.block.unreachable")

        # Show pending
        for uri in pending_uris:
            emit("detail", "Pending: {calendar}", calendar=uri, name="upgrade.pending")

        # Recap
        confirmed = len(bitcoin_atts)
        total = confirmed + len(pending_uris)
        if confirmed >= 2:
            emit("step_ok", "{confirmed}/{total} attestations confirmed, {pending} pending",
                 confirmed=confirmed, total=total, pending=len(pending_uris),
                 name="upgrade.recap")
        elif confirmed == 1:
            emit("warn", "{confirmed}/{total} attestations confirmed, {pending} pending",
                 confirmed=confirmed, total=total, pending=len(pending_uris),
                 name="upgrade.recap")
        else:
            emit("error", "0/{total} attestations confirmed",
                 total=total, name="upgrade.recap")

        # Save headers
        if args.save_header and verified_headers:
            header_path = Path(str(ots_path).removesuffix(".ots") + ".blockheader.json")

            # Append to existing file if present
            existing = {"file_digest": hexlify(detached.file_digest).decode(), "blocks": []}
            if header_path.exists():
                with open(header_path) as f:
                    existing = json.load(f)

            known_heights = {b["height"] for b in existing["blocks"]}
            for h in verified_headers:
                if h["height"] not in known_heights:
                    existing["blocks"].append(h)

            with open(header_path, "w") as f:
                json.dump(existing, f, indent=2)
            emit("detail_ok", "Block headers saved: {path} ({n} block(s))",
                 path=str(header_path), n=len(existing["blocks"]),
                 name="upgrade.header.saved")


def _cmd_verify(args):
    from ots_verify import verify_file

    ok = verify_file(args.file.resolve(), args.ots.resolve())
    if not ok:
        sys.exit(1)


def _cmd_info(args):
    ots_path = args.file.resolve()
    if not ots_path.exists():
        emit("error", f"OTS file not found: {ots_path}", name="info.not_found")
        sys.exit(1)

    try:
        info = get_ots_info(ots_path)
    except Exception as e:
        emit("error", f"Failed to read OTS file: {e}", name="info.error")
        sys.exit(1)

    emit("detail", f"File hash ({info['hash_algo']}): {info['file_hash']}", name="info.hash")

    status = "complete (Bitcoin-attested)" if info["complete"] else "pending"
    if info["complete"]:
        emit("step_ok", f"Status: {status}", name="info.status")
    else:
        emit("warn", f"Status: {status}", name="info.status")

    for att in info["attestations"]:
        if att["type"] == "bitcoin":
            emit("detail_ok", f"Bitcoin block: {att['block_height']}", name="info.attestation.bitcoin")
        elif att["type"] == "pending":
            emit("detail", f"Pending: {att['calendar']}", name="info.attestation.pending")

    chains = info["chains"]
    emit("detail", f"Proof chains: {len(chains)}", name="info.chains")
    for i, chain in enumerate(chains):
        if chain["type"] == "bitcoin":
            emit("detail", f"  Chain {i+1}: {chain['ops']} operations -> Bitcoin block {chain['block_height']}",
                 name="info.chain")
        elif chain["type"] == "pending":
            emit("detail", f"  Chain {i+1}: {chain['ops']} operations -> Pending ({chain['calendar']})",
                 name="info.chain")


# ---------------------------------------------------------------------------
# Unified parser + entry point
# ---------------------------------------------------------------------------

SORTED_ALGOS = sorted(SUPPORTED_ALGOS.keys())
_HANDLERS = {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zp modules run ots_timestamp",
        description="OpenTimestamps module — Bitcoin-anchored timestamps",
    )
    sub = parser.add_subparsers(dest="command")

    # Pipeline (hidden from --help)
    run_p = sub.add_parser("run", help=argparse.SUPPRESS)
    run_p.add_argument("--input", required=True)

    check_p = sub.add_parser("check", help=argparse.SUPPRESS)
    check_p.add_argument("--config")

    # Pipeline: job (hidden from --help)
    job_p = sub.add_parser("job", help=argparse.SUPPRESS)
    job_p.add_argument("--input", required=True)

    # Standalone: stamp
    stamp_p = sub.add_parser("stamp", help="Stamp file(s) via OpenTimestamps")
    stamp_p.add_argument("files", nargs="+", type=Path, help="File(s) to stamp")
    stamp_p.add_argument("--algo", default="sha256", choices=SORTED_ALGOS,
                         help="Hash algorithm (default: sha256)")
    stamp_p.add_argument("--no-nonce", action="store_true", default=False,
                         help="Don't add privacy nonce (calendar sees the real hash)")
    stamp_p.add_argument("--output-dir", type=Path, default=None,
                         help="Output directory (default: same as input file)")
    stamp_p.add_argument("--calendar-urls", nargs="*", default=None,
                         help="Calendar server URLs (default: OTS pool)")

    # Standalone: upgrade
    upgrade_p = sub.add_parser("upgrade", help="Upgrade pending .ots proof(s)")
    upgrade_p.add_argument("files", nargs="+", type=Path, help=".ots file(s) to upgrade")
    upgrade_p.add_argument("--save-header", action="store_true", default=False,
                           help="Save Bitcoin block header as .blockheader.json")
    upgrade_p.add_argument("--calendar-urls", nargs="*", default=None,
                           help="Calendar server URLs")

    # Standalone: verify
    verify_p = sub.add_parser("verify", help="Verify file against .ots proof")
    verify_p.add_argument("file", type=Path, help="Original file")
    verify_p.add_argument("ots", type=Path, help=".ots proof file")

    # Standalone: info
    info_p = sub.add_parser("info", help="Display .ots proof metadata")
    info_p.add_argument("file", type=Path, help=".ots file")

    _HANDLERS.update({
        "run": _cmd_run, "check": _cmd_check, "job": _cmd_job,
        "stamp": _cmd_stamp, "upgrade": _cmd_upgrade,
        "verify": _cmd_verify, "info": _cmd_info,
    })
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
