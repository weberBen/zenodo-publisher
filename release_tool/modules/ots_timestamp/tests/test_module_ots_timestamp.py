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


# ---------------------------------------------------------------------------
# Job subcommand tests — upgrade logic
# ---------------------------------------------------------------------------

def _make_job_input(output_dir: Path, ots_path: Path,
                    config_key: str = "manifest",
                    module_config: dict | None = None) -> dict:
    """Build input JSON for the job subcommand."""
    return {
        "config": {"identity_hash_algo": "sha256"},
        "output_dir": str(output_dir),
        "files": [{
            "file_path": str(ots_path),
            "config_key": config_key,
            "hashes": {},
            "module_config": module_config or {},
        }],
    }


def _run_job_subprocess(input_data: dict, tmp_path: Path):
    """Run module job subcommand as subprocess. Returns (events, result, rc).

    NOTE: patches applied in the test process do NOT affect the subprocess.
    Use _run_job_inprocess() for tests that need mocking.
    """
    input_file = tmp_path / "job_input.json"
    input_file.write_text(json.dumps(input_data))
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["PYTHONPATH"] = str(MODULE_DIR.parent)
    proc = subprocess.run(
        ["uv", "run", "--project", str(MODULE_DIR),
         str(MODULE_DIR / "ots_timestamp.py"), "job", "--input", str(input_file)],
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


def _run_job_inprocess(input_data: dict, tmp_path: Path):
    """Run module job handler in-process so that unittest.mock patches work.

    Returns (events, result, rc).
    """
    import io
    import argparse

    input_file = tmp_path / "job_input.json"
    input_file.write_text(json.dumps(input_data))

    captured = io.StringIO()
    args = argparse.Namespace(input=str(input_file))

    old_stdout = sys.stdout
    sys.stdout = captured
    rc = 0
    try:
        mod._cmd_job(args)
    except SystemExit as e:
        rc = e.code or 0
    except Exception:
        rc = 1
    finally:
        sys.stdout = old_stdout

    events, result = [], None
    for line in captured.getvalue().splitlines():
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
    return events, result, rc


def _create_pending_ots(tmp_path: Path, filename: str = "test.bin") -> Path:
    """Create a real pending .ots file using mocked create_timestamp."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    dummy = tmp_path / filename
    dummy.write_bytes(b"job test content")
    with patch("ots_timestamp.create_timestamp", side_effect=_mock_create_timestamp):
        ots_path = mod.stamp_file(dummy, tmp_path, calendar_urls=["http://fake"], nonce=False)
    return ots_path


def test_job_pending_ots_returns_pending(tmp_path):
    """job on a pending .ots that can't upgrade returns status=pending."""
    ots_path = _create_pending_ots(tmp_path)

    data = _make_job_input(tmp_path, ots_path)
    # Mock upgrade_timestamp to return False (no change)
    with patch("ots_timestamp.upgrade_timestamp", return_value=False):
        events, result, rc = _run_job_inprocess(data, tmp_path)

    assert rc == 0, f"job failed (rc={rc}). Events: {events}"
    assert result is not None
    assert result["status"] == "pending"
    assert _find(events, "job.upgrade.pending") or _find(events, "job.upgrade.start")


def test_job_already_complete_returns_complete(tmp_path):
    """job on an already-complete .ots returns status=complete immediately."""
    ots_path = _create_pending_ots(tmp_path)

    # Patch _is_complete to return True (simulate already upgraded)
    with patch("ots_timestamp.is_ots_complete", return_value=True):
        data = _make_job_input(tmp_path, ots_path)
        events, result, rc = _run_job_inprocess(data, tmp_path)

    assert rc == 0
    assert result["status"] == "complete"
    assert _find(events, "job.already_complete")


def test_job_missing_file_returns_error(tmp_path):
    """job on a non-existent .ots file returns status=error."""
    fake_path = tmp_path / "nonexistent.ots"
    data = _make_job_input(tmp_path, fake_path)
    events, result, rc = _run_job_inprocess(data, tmp_path)

    assert rc == 0
    assert result["status"] == "error"
    assert _find(events, "job.missing")


def test_job_upgrade_success_returns_complete(tmp_path):
    """job upgrades a pending .ots to complete."""
    ots_path = _create_pending_ots(tmp_path)

    def _mock_upgrade(timestamp, args):
        """Mock upgrade that adds a BitcoinBlockHeaderAttestation."""
        from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
        timestamp.attestations.add(BitcoinBlockHeaderAttestation(height=900000))
        return True

    data = _make_job_input(tmp_path, ots_path)
    with patch("ots_timestamp.upgrade_timestamp", side_effect=_mock_upgrade):
        events, result, rc = _run_job_inprocess(data, tmp_path)

    assert rc == 0
    assert result["status"] == "complete"
    assert _find(events, "job.upgrade.complete")


def test_job_upgrade_partial_returns_pending(tmp_path):
    """job partially upgrades (changed but still pending) returns pending."""
    ots_path = _create_pending_ots(tmp_path)

    def _mock_upgrade(timestamp, args):
        """Mock upgrade that changes something but doesn't add Bitcoin attestation."""
        return True  # changed, but no BitcoinBlockHeaderAttestation added

    data = _make_job_input(tmp_path, ots_path)
    with patch("ots_timestamp.upgrade_timestamp", side_effect=_mock_upgrade):
        events, result, rc = _run_job_inprocess(data, tmp_path)

    assert rc == 0
    assert result["status"] == "pending"
    assert _find(events, "job.upgrade.partial")


def test_job_upgrade_exception_returns_error(tmp_path):
    """job returns error when upgrade_ots raises."""
    ots_path = _create_pending_ots(tmp_path)

    data = _make_job_input(tmp_path, ots_path)
    with patch("ots_timestamp.upgrade_timestamp", side_effect=RuntimeError("network down")):
        events, result, rc = _run_job_inprocess(data, tmp_path)

    assert rc == 0
    assert result["status"] == "error"
    assert _find(events, "job.upgrade.error")


def test_job_save_header_on_complete(tmp_path):
    """job saves block header when upgrade.save_header=true and upgrade completes."""
    ots_path = _create_pending_ots(tmp_path)

    def _mock_upgrade(timestamp, args):
        from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
        timestamp.attestations.add(BitcoinBlockHeaderAttestation(height=900001))
        return True

    data = _make_job_input(tmp_path, ots_path, module_config={
        "upgrade": {"save_header": True},
    })
    with patch("ots_timestamp.upgrade_timestamp", side_effect=_mock_upgrade), \
         patch("ots_timestamp.verify_block_attestation", return_value={
             "hash": "abc", "height": 900001, "merkle_root": "def",
             "timestamp": 1700000000, "raw_header": "00" * 80,
             "previousblockhash": "ghi", "nonce": 123, "bits": 456,
         }):
        events, result, rc = _run_job_inprocess(data, tmp_path)

    assert rc == 0
    assert result["status"] == "complete"
    header_path = Path(str(ots_path).removesuffix(".ots") + ".blockheader.json")
    assert header_path.exists(), f"Expected block header file at {header_path}"
    header_data = json.loads(header_path.read_text())
    assert len(header_data["blocks"]) >= 1
    assert header_data["blocks"][0]["height"] == 900001


def test_job_no_header_by_default(tmp_path):
    """job does NOT save block header when save_header is not set."""
    ots_path = _create_pending_ots(tmp_path)

    def _mock_upgrade(timestamp, args):
        from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
        timestamp.attestations.add(BitcoinBlockHeaderAttestation(height=900002))
        return True

    data = _make_job_input(tmp_path, ots_path)  # no save_header in config
    with patch("ots_timestamp.upgrade_timestamp", side_effect=_mock_upgrade):
        events, result, rc = _run_job_inprocess(data, tmp_path)

    assert rc == 0
    assert result["status"] == "complete"
    header_path = Path(str(ots_path).removesuffix(".ots") + ".blockheader.json")
    assert not header_path.exists()


def test_job_multiple_files(tmp_path):
    """job processes multiple .ots files in one call."""
    ots1 = _create_pending_ots(tmp_path / "sub1", "file1.bin")
    ots2 = _create_pending_ots(tmp_path / "sub2", "file2.bin")

    data = {
        "config": {"identity_hash_algo": "sha256"},
        "output_dir": str(tmp_path),
        "files": [
            {"file_path": str(ots1), "config_key": "paper", "hashes": {}, "module_config": {}},
            {"file_path": str(ots2), "config_key": "manifest", "hashes": {}, "module_config": {}},
        ],
    }
    # Both stay pending
    with patch("ots_timestamp.upgrade_timestamp", return_value=False):
        events, result, rc = _run_job_inprocess(data, tmp_path)

    assert rc == 0
    assert result["status"] == "pending"
    done_events = [e for e in events if "job.upgrade" in (e.get("name") or "")]
    assert len(done_events) >= 2


def test_job_module_config_calendars_passed(tmp_path):
    """job passes module_config.calendars to upgrade_ots."""
    ots_path = _create_pending_ots(tmp_path)
    custom_calendars = ["https://custom.calendar.test"]

    data = _make_job_input(tmp_path, ots_path, module_config={
        "calendars": custom_calendars,
    })

    captured_urls = []

    def _capture_upgrade(path, calendar_urls=None):
        captured_urls.append(calendar_urls)
        return False  # no change

    with patch("ots_timestamp.upgrade_ots", side_effect=_capture_upgrade):
        events, result, rc = _run_job_inprocess(data, tmp_path)

    assert rc == 0
    assert captured_urls[0] == custom_calendars


@pytest.mark.network
def test_live_job_pending_upgrade(tmp_path):
    """Live: job on a freshly stamped .ots stays pending (too recent for Bitcoin)."""
    dummy = tmp_path / "live_test.bin"
    dummy.write_bytes(b"live job test")

    # Stamp first
    _, rc = _run_standalone(
        ["stamp", str(dummy), "--output-dir", str(tmp_path)], tmp_path)
    assert rc == 0

    ots_path = tmp_path / "live_test.bin.ots"
    assert ots_path.exists()

    # Run job — should stay pending (freshly stamped, no Bitcoin block yet)
    data = _make_job_input(tmp_path, ots_path)
    events, result, rc = _run_job_subprocess(data, tmp_path)

    assert rc == 0, f"job failed (rc={rc}). Events: {events}"
    assert result["status"] == "pending"
