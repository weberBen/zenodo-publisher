"""Tests for the digicert_timestamp built-in module.

Two kinds of tests:
  - Unit tests  (no network) — mock requests/rfc3161ng, test logic and error handling
  - Live tests  (network)    — real DigiCert TSA calls, verify output with openssl

Run all:
    uv run pytest test_module_digicert_timestamp.py -v

Run only live tests:
    uv run pytest test_module_digicert_timestamp.py -v -m network

Run only unit tests:
    uv run pytest test_module_digicert_timestamp.py -v -m "not network"
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

MODULE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(MODULE_DIR))
import digicert_timestamp as mod
import verify_tsr


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

VALID_SHA256 = "a" * 64  # 32 zero-like bytes — valid sha256 hex length


def _make_input(output_dir: Path, algo: str = "sha256", hex_hash: str = VALID_SHA256,
                config_key: str = "paper", full_chain: bool = True,
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
                "module_config": {"full_chain": full_chain},
            }
        ],
    }


def _find(events: list[dict], name: str) -> dict | None:
    return next((e for e in events if e.get("name") == name), None)


# ---------------------------------------------------------------------------
# Unit tests — mocked network
# ---------------------------------------------------------------------------

def test_check_valid_full_chain_true(capsys):
    """check() avec full_chain=True émet check.ok sans erreur."""
    mod.check({"full_chain": True})
    event = json.loads(capsys.readouterr().out.strip())
    assert event["name"] == "digicert_timestamp.check.ok"
    assert event["type"] == "detail_ok"


def test_check_valid_full_chain_false(capsys):
    """check() avec full_chain=False émet check.ok sans erreur."""
    mod.check({"full_chain": False})
    event = json.loads(capsys.readouterr().out.strip())
    assert event["name"] == "digicert_timestamp.check.ok"


def test_check_valid_default(capsys):
    """check() sans config utilise le défaut full_chain=True et passe."""
    mod.check({})
    event = json.loads(capsys.readouterr().out.strip())
    assert event["name"] == "digicert_timestamp.check.ok"


def test_check_invalid_full_chain_string(capsys):
    """check() avec full_chain="yes" (string) émet check.invalid_config et exit 1."""
    with pytest.raises(SystemExit) as exc:
        mod.check({"full_chain": "yes"})
    assert exc.value.code == 1
    event = json.loads(capsys.readouterr().out.strip())
    assert event["name"] == "digicert_timestamp.check.invalid_config"
    assert event["type"] == "error"


def test_check_invalid_full_chain_int(capsys):
    """check() avec full_chain=1 (int) émet une erreur et exit 1 (doit être bool)."""
    with pytest.raises(SystemExit) as exc:
        mod.check({"full_chain": 1})
    assert exc.value.code == 1


def test_request_timestamp_produces_tsr(tmp_path):
    """request_timestamp() crée un fichier <filename>.tsr avec le contenu de la réponse HTTP."""
    tsr_content = b"fake-tsr-bytes"
    mock_resp = MagicMock()
    mock_resp.content = tsr_content
    mock_resp.raise_for_status = MagicMock()

    with patch("digicert_timestamp.requests.post", return_value=mock_resp), \
         patch("digicert_timestamp.rfc3161ng.make_timestamp_request", return_value=MagicMock()), \
         patch("digicert_timestamp.rfc3161ng.encode_timestamp_request", return_value=b"req"):
        tsr_path = mod.request_timestamp(VALID_SHA256, "sha256", True, tmp_path, "paper.pdf")

    assert tsr_path == tmp_path / "paper.pdf.tsr"
    assert tsr_path.read_bytes() == tsr_content


def test_request_timestamp_correct_params(tmp_path):
    """request_timestamp() passe digest=bytes.fromhex(hex), hashname et include_tsa_certificate à rfc3161ng,
    puis encode_timestamp_request() avant le POST."""
    mock_tsq = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = b"tsr"
    mock_resp.raise_for_status = MagicMock()

    with patch("digicert_timestamp.requests.post", return_value=mock_resp), \
         patch("digicert_timestamp.rfc3161ng.make_timestamp_request", return_value=mock_tsq) as mock_make, \
         patch("digicert_timestamp.rfc3161ng.encode_timestamp_request", return_value=b"encoded") as mock_enc:
        mod.request_timestamp(VALID_SHA256, "sha256", False, tmp_path, "paper.pdf")

    mock_make.assert_called_once_with(
        digest=bytes.fromhex(VALID_SHA256),
        hashname="sha256",
        include_tsa_certificate=False,
    )
    mock_enc.assert_called_once_with(mock_tsq)


def test_request_timestamp_http_error(tmp_path):
    """request_timestamp() propage l'exception HTTP si le serveur retourne une erreur."""
    import requests as req_lib
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = req_lib.HTTPError("500")

    with patch("digicert_timestamp.requests.post", return_value=mock_resp), \
         patch("digicert_timestamp.rfc3161ng.make_timestamp_request", return_value=MagicMock()), \
         patch("digicert_timestamp.rfc3161ng.encode_timestamp_request", return_value=b"req"):
        with pytest.raises(req_lib.HTTPError):
            mod.request_timestamp(VALID_SHA256, "sha256", True, tmp_path, "paper.pdf")


def test_main_check_valid(capsys):
    """main --check sans --config émet check.ok et retourne 0."""
    sys.argv = ["digicert_timestamp.py", "--check"]
    mod.main()
    event = json.loads(capsys.readouterr().out.strip())
    assert event["name"] == "digicert_timestamp.check.ok"


def test_main_check_invalid_config(tmp_path, capsys):
    """main --check --config <file> avec full_chain invalide émet check.invalid_config et exit 1."""
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"module_config": {"full_chain": "bad"}}))
    sys.argv = ["digicert_timestamp.py", "--check", "--config", str(cfg)]
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1
    event = json.loads(capsys.readouterr().out.strip())
    assert event["name"] == "digicert_timestamp.check.invalid_config"


def test_main_unsupported_algo(tmp_path, capsys):
    """main --input avec identity_hash_algo=md5 (non supporté RFC 3161) émet unsupported_algo et exit 1."""
    data = _make_input(tmp_path, algo="md5", hex_hash="a" * 32)
    data["files"][0]["hashes"] = {"md5": {"type": "md5", "value": "a" * 32,
                                           "formatted_value": "md5:" + "a" * 32}}
    f = tmp_path / "input.json"
    f.write_text(json.dumps(data))
    sys.argv = ["digicert_timestamp.py", "--input", str(f)]
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1
    event = json.loads(capsys.readouterr().out.strip())
    assert event["name"] == "digicert_timestamp.unsupported_algo"


def test_main_missing_hash(tmp_path, capsys):
    """main --input où identity_hash_algo=sha256 mais seul md5 est dans hashes émet missing_hash et exit 1."""
    data = _make_input(tmp_path)
    data["files"][0]["hashes"] = {"md5": {"type": "md5", "value": "a" * 32,
                                           "formatted_value": "md5:" + "a" * 32}}
    f = tmp_path / "input.json"
    f.write_text(json.dumps(data))
    sys.argv = ["digicert_timestamp.py", "--input", str(f)]
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1
    event = json.loads(capsys.readouterr().out.strip())
    assert event["name"] == "digicert_timestamp.missing_hash"
    assert event["data"]["algo"] == "sha256"


def test_main_success_result(tmp_path, capsys):
    """main --input réussi retourne un result JSON avec file_path, config_key et module_entry_type corrects."""
    data = _make_input(tmp_path)
    f = tmp_path / "input.json"
    f.write_text(json.dumps(data))

    mock_resp = MagicMock()
    mock_resp.content = b"fake-tsr"
    mock_resp.raise_for_status = MagicMock()

    sys.argv = ["digicert_timestamp.py", "--input", str(f)]
    with patch("digicert_timestamp.requests.post", return_value=mock_resp), \
         patch("digicert_timestamp.rfc3161ng.make_timestamp_request", return_value=MagicMock()), \
         patch("digicert_timestamp.rfc3161ng.encode_timestamp_request", return_value=b"req"):
        mod.main()

    lines = [l for l in capsys.readouterr().out.strip().splitlines() if l.strip()]
    events = [json.loads(l) for l in lines]
    result = next(e for e in events if e.get("type") == "result")
    rf = result["files"][0]
    assert rf["config_key"] == "paper"
    assert rf["module_entry_type"] == "tsr"
    assert rf["file_path"].endswith("paper.pdf.tsr")


def test_main_success_events(tmp_path, capsys):
    """main --input réussi émet les events digicert_timestamp.start et digicert_timestamp.done."""
    data = _make_input(tmp_path)
    f = tmp_path / "input.json"
    f.write_text(json.dumps(data))

    mock_resp = MagicMock()
    mock_resp.content = b"fake-tsr"
    mock_resp.raise_for_status = MagicMock()

    sys.argv = ["digicert_timestamp.py", "--input", str(f)]
    with patch("digicert_timestamp.requests.post", return_value=mock_resp), \
         patch("digicert_timestamp.rfc3161ng.make_timestamp_request", return_value=MagicMock()), \
         patch("digicert_timestamp.rfc3161ng.encode_timestamp_request", return_value=b"req"):
        mod.main()

    lines = [l for l in capsys.readouterr().out.strip().splitlines() if l.strip()]
    names = [json.loads(l).get("name") for l in lines]
    assert "digicert_timestamp.start" in names
    assert "digicert_timestamp.done" in names


def test_main_tsa_error(tmp_path, capsys):
    """main --input émet digicert_timestamp.tsa_error et exit 1 si le POST au TSA retourne une erreur HTTP."""
    import requests as req_lib
    data = _make_input(tmp_path)
    f = tmp_path / "input.json"
    f.write_text(json.dumps(data))

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = req_lib.HTTPError("503")

    sys.argv = ["digicert_timestamp.py", "--input", str(f)]
    with patch("digicert_timestamp.requests.post", return_value=mock_resp), \
         patch("digicert_timestamp.rfc3161ng.make_timestamp_request", return_value=MagicMock()), \
         patch("digicert_timestamp.rfc3161ng.encode_timestamp_request", return_value=b"req"):
        with pytest.raises(SystemExit) as exc:
            mod.main()
    assert exc.value.code == 1
    lines = [l for l in capsys.readouterr().out.strip().splitlines() if l.strip()]
    names = [json.loads(l).get("name") for l in lines]
    assert "digicert_timestamp.tsa_error" in names


def test_main_full_chain_false_forwarded(tmp_path, capsys):
    """main --input avec full_chain=False dans module_config transmet include_tsa_certificate=False à rfc3161ng."""
    data = _make_input(tmp_path, full_chain=False)
    f = tmp_path / "input.json"
    f.write_text(json.dumps(data))

    mock_resp = MagicMock()
    mock_resp.content = b"tsr"
    mock_resp.raise_for_status = MagicMock()

    sys.argv = ["digicert_timestamp.py", "--input", str(f)]
    with patch("digicert_timestamp.requests.post", return_value=mock_resp), \
         patch("digicert_timestamp.rfc3161ng.make_timestamp_request", return_value=MagicMock()) as mock_make, \
         patch("digicert_timestamp.rfc3161ng.encode_timestamp_request", return_value=b"req"):
        mod.main()

    _, kwargs = mock_make.call_args
    assert kwargs["include_tsa_certificate"] is False


# ---------------------------------------------------------------------------
# Live integration tests — real DigiCert TSA calls, requires network
# ---------------------------------------------------------------------------

def _run_module(input_data: dict, tmp_path: Path):
    """Run module as subprocess (same as ZP) and return (events, result, returncode)."""
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps(input_data))
    proc = subprocess.run(
        ["uv", "run", "--project", str(MODULE_DIR),
         str(MODULE_DIR / "digicert_timestamp.py"), "--input", str(input_file)],
        capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"},
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


def _openssl_pkcs7_certs(tsr_path: Path) -> str:
    """Extract embedded certs from TSR via openssl pkcs7."""
    token = subprocess.run(
        ["openssl", "ts", "-reply", "-in", str(tsr_path), "-token_out"],
        capture_output=True,
    )
    pkcs7 = subprocess.run(
        ["openssl", "pkcs7", "-inform", "DER", "-print_certs", "-noout"],
        input=token.stdout, capture_output=True,
    )
    return pkcs7.stdout.decode(errors="replace")


@pytest.mark.network
def test_live_tsr_produced(tmp_path):
    """Module produces a .tsr file for a real file."""
    dummy = tmp_path / "dummy.bin"
    dummy.write_bytes(b"hello zenodo-publisher")
    hex_hash = hashlib.sha256(dummy.read_bytes()).hexdigest()

    data = _make_input(tmp_path, hex_hash=hex_hash, file_path=str(dummy))
    events, result, rc = _run_module(data, tmp_path)

    assert rc == 0, f"Module exited {rc}. Events: {events}"
    assert result is not None, "No result line in output"
    assert len(result["files"]) == 1
    tsr_path = Path(result["files"][0]["file_path"])
    assert tsr_path.exists(), f"TSR file not found: {tsr_path}"
    assert tsr_path.stat().st_size > 0


@pytest.mark.network
def test_live_hash_in_tsr_matches_input(tmp_path):
    """The hash embedded in the TSR matches the sha256 we sent."""
    dummy = tmp_path / "dummy.bin"
    dummy.write_bytes(b"hello zenodo-publisher")
    hex_hash = hashlib.sha256(dummy.read_bytes()).hexdigest()

    data = _make_input(tmp_path, hex_hash=hex_hash, file_path=str(dummy))
    _, result, rc = _run_module(data, tmp_path)
    assert rc == 0

    tsr_path = Path(result["files"][0]["file_path"])
    tsr_hash = verify_tsr.parse_tsr_hash(tsr_path)

    assert tsr_hash == hex_hash, (
        f"Hash mismatch:\n  sent:      {hex_hash}\n  in TSR:    {tsr_hash}"
    )


@pytest.mark.network
def test_live_algo_is_sha256(tmp_path):
    """openssl reports sha256 as the hash algorithm in the TSR."""
    dummy = tmp_path / "dummy.bin"
    dummy.write_bytes(b"test algo check")
    hex_hash = hashlib.sha256(dummy.read_bytes()).hexdigest()

    data = _make_input(tmp_path, hex_hash=hex_hash, file_path=str(dummy))
    _, result, rc = _run_module(data, tmp_path)
    assert rc == 0

    tsr_path = Path(result["files"][0]["file_path"])
    tsr_text = verify_tsr.run(["openssl", "ts", "-reply", "-in", str(tsr_path), "-text"]).stdout
    assert "sha256" in tsr_text.lower(), \
        f"Expected sha256 in TSR. openssl output:\n{tsr_text}"


@pytest.mark.network
def test_live_full_chain_true_embeds_certs(tmp_path):
    """full_chain=True: cert chain (≥2 certs) embedded in TSR."""
    dummy = tmp_path / "dummy.bin"
    dummy.write_bytes(b"full chain test")
    hex_hash = hashlib.sha256(dummy.read_bytes()).hexdigest()

    data = _make_input(tmp_path, hex_hash=hex_hash, file_path=str(dummy), full_chain=True)
    _, result, rc = _run_module(data, tmp_path)
    assert rc == 0

    cert_output = _openssl_pkcs7_certs(Path(result["files"][0]["file_path"]))
    subjects = [l for l in cert_output.splitlines() if l.startswith("subject=")]
    assert len(subjects) >= 2, \
        f"Expected ≥2 certs with full_chain=True. Got:\n{cert_output}"


@pytest.mark.network
def test_live_full_chain_false_no_certs(tmp_path):
    """full_chain=False: no cert chain embedded in TSR."""
    dummy = tmp_path / "dummy.bin"
    dummy.write_bytes(b"no chain test")
    hex_hash = hashlib.sha256(dummy.read_bytes()).hexdigest()

    data = _make_input(tmp_path, hex_hash=hex_hash, file_path=str(dummy), full_chain=False)
    _, result, rc = _run_module(data, tmp_path)
    assert rc == 0

    cert_output = _openssl_pkcs7_certs(Path(result["files"][0]["file_path"]))
    subjects = [l for l in cert_output.splitlines() if l.startswith("subject=")]
    assert len(subjects) == 0, \
        f"Expected no embedded certs with full_chain=False. Got:\n{cert_output}"


@pytest.mark.network
def test_live_cert_is_digicert(tmp_path):
    """Embedded certificates are issued by DigiCert."""
    dummy = tmp_path / "dummy.bin"
    dummy.write_bytes(b"cert check")
    hex_hash = hashlib.sha256(dummy.read_bytes()).hexdigest()

    data = _make_input(tmp_path, hex_hash=hex_hash, file_path=str(dummy), full_chain=True)
    _, result, rc = _run_module(data, tmp_path)
    assert rc == 0

    cert_output = _openssl_pkcs7_certs(Path(result["files"][0]["file_path"]))
    assert "DigiCert" in cert_output, \
        f"Expected DigiCert in cert subjects. Got:\n{cert_output}"


@pytest.mark.network
def test_live_openssl_verify(tmp_path):
    """Full openssl ts -verify passes using root CA extracted from TSR itself."""
    dummy = tmp_path / "dummy.bin"
    dummy.write_bytes(b"openssl verify test")
    hex_hash = hashlib.sha256(dummy.read_bytes()).hexdigest()

    data = _make_input(tmp_path, hex_hash=hex_hash, file_path=str(dummy), full_chain=True)
    _, result, rc = _run_module(data, tmp_path)
    assert rc == 0

    tsr_path = Path(result["files"][0]["file_path"])
    chain_pem = tmp_path / "chain.pem"
    full_chain_pem = tmp_path / "full_chain.pem"
    verify_tsr.extract_chain(tsr_path, chain_pem)
    verify_tsr.build_full_chain(chain_pem, full_chain_pem)

    r = verify_tsr.verify(dummy, tsr_path, full_chain_pem)
    assert r.returncode == 0, f"openssl ts -verify failed:\n{r.stderr}"


@pytest.mark.network
def test_live_check_mode(tmp_path):
    """--check mode exits 0 and emits check.ok."""
    proc = subprocess.run(
        ["uv", "run", "--project", str(MODULE_DIR),
         str(MODULE_DIR / "digicert_timestamp.py"), "--check"],
        capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"},
    )
    assert proc.returncode == 0, f"--check failed: {proc.stdout} {proc.stderr}"
    event = json.loads(proc.stdout.strip())
    assert event["name"] == "digicert_timestamp.check.ok"


@pytest.mark.network
def test_live_multiple_files(tmp_path):
    """Module correctly timestamps multiple files in one call."""
    files = []
    for i in range(3):
        f = tmp_path / f"file_{i}.bin"
        f.write_bytes(f"content {i}".encode())
        files.append({
            "file_path": str(f),
            "config_key": f"file_{i}",
            "type": "file",
            "hashes": {
                "sha256": {
                    "type": "sha256",
                    "value": hashlib.sha256(f.read_bytes()).hexdigest(),
                    "formatted_value": "sha256:" + hashlib.sha256(f.read_bytes()).hexdigest(),
                }
            },
            "module_config": {"full_chain": True},
        })

    data = {
        "config": {"identity_hash_algo": "sha256"},
        "output_dir": str(tmp_path),
        "files": files,
    }
    _, result, rc = _run_module(data, tmp_path)

    assert rc == 0
    assert len(result["files"]) == 3
    for rf in result["files"]:
        assert Path(rf["file_path"]).exists()
        assert rf["module_entry_type"] == "tsr"
