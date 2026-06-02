"""Tests for the ots_timestamp built-in module.

Two kinds of tests:
  - Unit tests  (no network) — mock create_timestamp, test logic and error handling
  - Live tests  (network)    — real OTS calendar server calls

Run all:
    uv run pytest tests/test_module_ots_timestamp.py -v

Run only live tests:
    uv run pytest tests/test_module_ots_timestamp.py -v -m network

Run only unit tests:
    uv run pytest tests/test_module_ots_timestamp.py -v -m "not network"
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

MODULE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MODULE_DIR))
# _shared must be importable
_modules_dir = str(MODULE_DIR.parent)
if _modules_dir not in sys.path:
    sys.path.insert(0, _modules_dir)

import ots_timestamp as mod


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

VALID_SHA256 = "a" * 64


def _make_input(output_dir: Path, algo: str = "sha256", hex_hash: str = VALID_SHA256,
                config_key: str = "paper", nonce: bool = True,
                file_path: str | None = None) -> dict:
    fp = file_path or str(output_dir / "paper.pdf")
    return {
        "config": {"identity_hash_algo": algo},
        "output_dir": str(output_dir),
        "files": [
            {
                "file_path": fp,
                "config_key": config_key,
                "type": "file",
                "hashes": {
                    algo: {"type": algo, "value": hex_hash,
                           "formatted_value": f"{algo}:{hex_hash}"},
                },
                "module_config": {"nonce": nonce},
            }
        ],
    }


def _parse_events(stdout: str) -> list[dict]:
    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    return events


def _find(events: list[dict], name: str) -> dict | None:
    return next((e for e in events if name in (e.get("name") or "")), None)


# ---------------------------------------------------------------------------
# Unit tests — mocked network
# ---------------------------------------------------------------------------

def _mock_create_timestamp(timestamp, calendar_urls, args):
    """Mock create_timestamp that adds a fake PendingAttestation (no network)."""
    from opentimestamps.core.notary import PendingAttestation
    timestamp.attestations.add(PendingAttestation("https://fake.calendar.test"))


def test_main_check_valid(capsys):
    """check without config emits check.ok."""
    sys.argv = ["ots_timestamp.py", "check"]
    with patch("ots_timestamp.requests.get") as mock_get:
        mock_get.return_value.status_code = 200
        mod.main()
    events = _parse_events(capsys.readouterr().out)
    assert _find(events, "check.ok"), f"Expected check.ok. Events: {events}"


def test_main_success_result(tmp_path, capsys):
    """run produces result with file_path, config_key, module_entry_type=ots."""
    dummy = tmp_path / "paper.pdf"
    dummy.write_bytes(b"test content")

    data = _make_input(tmp_path, file_path=str(dummy))
    f = tmp_path / "input.json"
    f.write_text(json.dumps(data))

    sys.argv = ["ots_timestamp.py", "run", "--input", str(f)]
    with patch("ots_timestamp.create_timestamp", side_effect=_mock_create_timestamp):
        mod.main()

    events = _parse_events(capsys.readouterr().out)
    result = next((e for e in events if e.get("type") == "result"), None)
    assert result is not None, f"Expected result. Events: {events}"
    rf = result["files"][0]
    assert rf["config_key"] == "paper"
    assert rf["module_entry_type"] == "ots"
    assert rf["file_path"].endswith(".ots")


def test_main_success_events(tmp_path, capsys):
    """run emits start and done events."""
    dummy = tmp_path / "paper.pdf"
    dummy.write_bytes(b"test content")

    data = _make_input(tmp_path, file_path=str(dummy))
    f = tmp_path / "input.json"
    f.write_text(json.dumps(data))

    sys.argv = ["ots_timestamp.py", "run", "--input", str(f)]
    with patch("ots_timestamp.create_timestamp", side_effect=_mock_create_timestamp):
        mod.main()

    events = _parse_events(capsys.readouterr().out)
    assert _find(events, "ots_timestamp.start"), f"Expected start event. Events: {events}"
    assert _find(events, "ots_timestamp.done"), f"Expected done event. Events: {events}"


def test_unsupported_algo_fallback(tmp_path, capsys):
    """identity_hash_algo=md5 (not supported by OTS) → warn + fallback sha256."""
    dummy = tmp_path / "paper.pdf"
    dummy.write_bytes(b"test content")

    data = _make_input(tmp_path, algo="md5", hex_hash="a" * 32, file_path=str(dummy))
    f = tmp_path / "input.json"
    f.write_text(json.dumps(data))

    sys.argv = ["ots_timestamp.py", "run", "--input", str(f)]
    with patch("ots_timestamp.create_timestamp", side_effect=_mock_create_timestamp):
        mod.main()

    events = _parse_events(capsys.readouterr().out)
    warn = _find(events, "unsupported_algo")
    assert warn is not None, f"Expected unsupported_algo warn. Events: {events}"
    assert warn["type"] == "warn"


def test_missing_hash_fallback(tmp_path, capsys):
    """sha256 absent from hashes → compute_file_hash fallback."""
    dummy = tmp_path / "paper.pdf"
    dummy.write_bytes(b"test content")

    data = _make_input(tmp_path, file_path=str(dummy))
    data["files"][0]["hashes"] = {"md5": {"type": "md5", "value": "a" * 32,
                                           "formatted_value": "md5:" + "a" * 32}}
    f = tmp_path / "input.json"
    f.write_text(json.dumps(data))

    sys.argv = ["ots_timestamp.py", "run", "--input", str(f)]
    with patch("ots_timestamp.create_timestamp", side_effect=_mock_create_timestamp):
        mod.main()

    events = _parse_events(capsys.readouterr().out)
    result = next((e for e in events if e.get("type") == "result"), None)
    assert result is not None, f"Expected result (file hash fallback). Events: {events}"


def test_nonce_default(tmp_path):
    """stamp_file with default nonce=True adds nonce ops to the timestamp tree."""
    dummy = tmp_path / "test.bin"
    dummy.write_bytes(b"nonce test")

    with patch("ots_timestamp.create_timestamp", side_effect=_mock_create_timestamp):
        ots_path = mod.stamp_file(dummy, tmp_path, calendar_urls=["http://fake"], nonce=True)

    assert ots_path.exists()
    detached = mod._deserialize_ots(ots_path)
    # With nonce, the timestamp tree has ops (AppendOp + SHA256Op)
    assert len(detached.timestamp.ops) > 0, "Expected nonce ops in timestamp tree"


def test_nonce_disabled(tmp_path):
    """stamp_file with nonce=False: no extra ops added."""
    dummy = tmp_path / "test.bin"
    dummy.write_bytes(b"no nonce test")

    with patch("ots_timestamp.create_timestamp", side_effect=_mock_create_timestamp):
        ots_path = mod.stamp_file(dummy, tmp_path, calendar_urls=["http://fake"], nonce=False)

    assert ots_path.exists()
    detached = mod._deserialize_ots(ots_path)
    # Without nonce, create_timestamp is a no-op so no ops were added
    assert len(detached.timestamp.ops) == 0, "Expected no ops without nonce"


def test_get_ots_info_pending(tmp_path):
    """get_ots_info on a pending .ots returns correct metadata."""
    dummy = tmp_path / "test.bin"
    dummy.write_bytes(b"info test")

    with patch("ots_timestamp.create_timestamp", side_effect=_mock_create_timestamp):
        ots_path = mod.stamp_file(dummy, tmp_path, calendar_urls=["http://fake"], nonce=False)

    info = mod.get_ots_info(ots_path)
    assert info["hash_algo"] == "sha256"
    assert info["complete"] is False
    assert info["file_hash"] == hashlib.sha256(b"info test").hexdigest()


# ---------------------------------------------------------------------------
# Live integration tests — real OTS calendar server calls
# ---------------------------------------------------------------------------

def _run_module(input_data: dict, tmp_path: Path):
    """Run module as subprocess and return (events, result, returncode)."""
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps(input_data))
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["PYTHONPATH"] = str(MODULE_DIR.parent)
    proc = subprocess.run(
        ["uv", "run", "--project", str(MODULE_DIR),
         str(MODULE_DIR / "ots_timestamp.py"), "run", "--input", str(input_file)],
        capture_output=True, text=True, env=env,
    )
    events, result = [], None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            result = ev
        else:
            events.append(ev)
    return events, result, proc.returncode


def _run_standalone(args: list[str], tmp_path: Path):
    """Run module standalone subcommand and return (events, returncode)."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["PYTHONPATH"] = str(MODULE_DIR.parent)
    proc = subprocess.run(
        ["uv", "run", "--project", str(MODULE_DIR),
         str(MODULE_DIR / "ots_timestamp.py"), *args],
        capture_output=True, text=True, env=env,
    )
    events = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events, proc.returncode


@pytest.mark.network
def test_live_stamp_produces_ots(tmp_path):
    """stamp standalone produces a .ots file."""
    dummy = tmp_path / "test.bin"
    dummy.write_bytes(b"live stamp test")

    events, rc = _run_standalone(
        ["stamp", str(dummy), "--output-dir", str(tmp_path)], tmp_path)

    assert rc == 0, f"stamp failed (rc={rc}). Events: {events}"
    ots_path = tmp_path / "test.bin.ots"
    assert ots_path.exists(), "OTS file not produced"
    assert ots_path.stat().st_size > 0


@pytest.mark.network
def test_live_stamp_verify_roundtrip(tmp_path):
    """stamp then verify: hash match + pending status."""
    dummy = tmp_path / "test.bin"
    dummy.write_bytes(b"roundtrip test")

    _, rc = _run_standalone(
        ["stamp", str(dummy), "--output-dir", str(tmp_path)], tmp_path)
    assert rc == 0

    ots_path = tmp_path / "test.bin.ots"
    events, rc = _run_standalone(
        ["verify", str(dummy), str(ots_path)], tmp_path)

    assert rc == 0, f"verify failed (rc={rc}). Events: {events}"
    assert _find(events, "verify.hash.match"), \
        f"Expected hash match event. Events: {events}"
    assert _find(events, "verify.pending"), \
        f"Expected pending status (fresh stamp). Events: {events}"


@pytest.mark.network
def test_live_stamp_hash(tmp_path):
    """stamp_hash with pre-computed hash produces a valid .ots."""
    dummy = tmp_path / "test.bin"
    content = b"hash stamp test"
    dummy.write_bytes(content)
    hex_hash = hashlib.sha256(content).hexdigest()

    data = _make_input(tmp_path, hex_hash=hex_hash, file_path=str(dummy))
    events, result, rc = _run_module(data, tmp_path)

    assert rc == 0, f"Module failed (rc={rc}). Events: {events}"
    assert result is not None, "No result"
    ots_path = Path(result["files"][0]["file_path"])
    assert ots_path.exists()

    # Verify the stamp matches the file
    verify_events, verify_rc = _run_standalone(
        ["verify", str(dummy), str(ots_path)], tmp_path)
    assert _find(verify_events, "verify.hash.match"), \
        f"Hash should match. Events: {verify_events}"


@pytest.mark.network
def test_live_info_pending(tmp_path):
    """info on a pending .ots shows status pending and chains."""
    dummy = tmp_path / "test.bin"
    dummy.write_bytes(b"info test")

    _, rc = _run_standalone(
        ["stamp", str(dummy), "--output-dir", str(tmp_path)], tmp_path)
    assert rc == 0

    ots_path = tmp_path / "test.bin.ots"
    events, rc = _run_standalone(["info", str(ots_path)], tmp_path)

    assert rc == 0, f"info failed (rc={rc}). Events: {events}"
    assert _find(events, "info.hash"), f"Expected hash in info. Events: {events}"
    assert _find(events, "info.status"), f"Expected status in info. Events: {events}"
    assert _find(events, "info.chains"), f"Expected chains in info. Events: {events}"


@pytest.mark.network
def test_live_verify_wrong_file_fails(tmp_path):
    """verify with a different file fails with mismatch."""
    original = tmp_path / "original.bin"
    original.write_bytes(b"original content")
    tampered = tmp_path / "tampered.bin"
    tampered.write_bytes(b"tampered content")

    _, rc = _run_standalone(
        ["stamp", str(original), "--output-dir", str(tmp_path)], tmp_path)
    assert rc == 0

    ots_path = tmp_path / "original.bin.ots"
    events, rc = _run_standalone(
        ["verify", str(tampered), str(ots_path)], tmp_path)

    assert rc == 1, "verify should fail for tampered file"
    assert _find(events, "verify.hash.mismatch"), \
        f"Expected hash mismatch. Events: {events}"


@pytest.mark.network
def test_live_check_mode(tmp_path):
    """check mode verifies calendar reachability."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["PYTHONPATH"] = str(MODULE_DIR.parent)
    proc = subprocess.run(
        ["uv", "run", "--project", str(MODULE_DIR),
         str(MODULE_DIR / "ots_timestamp.py"), "check"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"check failed: {proc.stdout} {proc.stderr}"
    events = _parse_events(proc.stdout)
    assert _find(events, "check.ok"), f"Expected check.ok. Events: {events}"


@pytest.mark.network
def test_live_multiple_files(tmp_path):
    """Module correctly stamps multiple files in one call."""
    files = []
    for i in range(3):
        f = tmp_path / f"file_{i}.bin"
        f.write_bytes(f"content {i}".encode())
        hex_hash = hashlib.sha256(f.read_bytes()).hexdigest()
        files.append({
            "file_path": str(f),
            "config_key": f"file_{i}",
            "type": "file",
            "hashes": {
                "sha256": {
                    "type": "sha256",
                    "value": hex_hash,
                    "formatted_value": "sha256:" + hex_hash,
                }
            },
            "module_config": {"nonce": True},
        })

    data = {
        "config": {"identity_hash_algo": "sha256"},
        "output_dir": str(tmp_path),
        "files": files,
    }
    events, result, rc = _run_module(data, tmp_path)

    assert rc == 0, f"Module failed (rc={rc}). Events: {events}"
    assert result is not None
    assert len(result["files"]) == 3
    for rf in result["files"]:
        assert Path(rf["file_path"]).exists()
        assert rf["module_entry_type"] == "ots"
