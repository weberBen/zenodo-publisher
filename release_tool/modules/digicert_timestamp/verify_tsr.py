#!/usr/bin/env python3
"""Verify a RFC 3161 timestamp response (TSR) against a file."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from _shared import create_emitter

emit = create_emitter("digicert_timestamp")


def run(cmd: list[str], capture=True) -> subprocess.CompletedProcess:
    emit("cmd", " ".join(cmd), name="verify.cmd")
    return subprocess.run(cmd, capture_output=capture, text=True)


def extract_chain(tsr: Path, dest: Path):
    emit("detail", "Extracting certificate chain from TSR...", name="verify.extract_chain")
    cmd1 = ["openssl", "ts", "-reply", "-in", str(tsr), "-token_out"]
    cmd2 = ["openssl", "pkcs7", "-inform", "DER", "-print_certs", "-out", str(dest)]
    emit("cmd", f"{' '.join(cmd1)} | {' '.join(cmd2)}", name="verify.cmd")
    p1 = subprocess.Popen(
        cmd1, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    p2 = subprocess.Popen(
        cmd2, stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    p1.stdout.close()
    _, err = p2.communicate()
    p1.wait()
    if p2.returncode != 0:
        msg = f"Failed to extract certificate chain from TSR: {err.decode()}"
        emit("error", msg, name="verify.extract_chain.failed")
        raise RuntimeError(msg)
    emit("detail_ok", "Certificate chain extracted", name="verify.extract_chain.ok")


def print_chain_subjects(chain: Path):
    emit("detail", "Chain certificates:", name="verify.chain")
    r = run(["openssl", "crl2pkcs7", "-nocrl", "-certfile", str(chain)])
    cmd2 = ["openssl", "pkcs7", "-print_certs", "-noout"]
    emit("cmd", " ".join(cmd2), name="verify.cmd")
    r2 = subprocess.run(cmd2, input=r.stdout, capture_output=True, text=True)
    for line in r2.stdout.splitlines():
        if line.startswith(("subject=", "issuer=")):
            emit("detail", line, name="verify.chain.cert")


def get_root_issuer(chain: Path) -> str:
    r = run(["openssl", "crl2pkcs7", "-nocrl", "-certfile", str(chain)])
    cmd2 = ["openssl", "pkcs7", "-print_certs", "-noout"]
    emit("cmd", " ".join(cmd2), name="verify.cmd")
    r2 = subprocess.run(cmd2, input=r.stdout, capture_output=True, text=True)
    issuers = [l for l in r2.stdout.splitlines() if l.startswith("issuer=")]
    return issuers[-1].removeprefix("issuer=").strip() if issuers else ""

def build_full_chain(chain: Path, dest: Path, root_cert: Path = None, raise_if_not_found: bool = False):
    certs_dir = Path("/etc/ssl/certs")
    root_issuer = get_root_issuer(chain)
    emit("detail", f"Looking for root CA: {root_issuer}", name="verify.root_ca")

    if not root_cert:
        emit("detail", "Auto search of root certificate",
                 name="verify.build_chain.root_cert.finding.auto")

        root_pem = None
        for cert_file in certs_dir.glob("*.pem"):
            r = run(["openssl", "x509", "-in", str(cert_file), "-noout", "-subject"])
            if r.returncode == 0 and root_issuer in r.stdout:
                root_pem = cert_file.read_text()
                break
    else:
        emit("detail", f"Using provided root certificate: {root_cert}",
                 name="verify.build_chain.root_cert.finding.manual")

        if not root_cert.exists():
            msg = f"Root certificate not found: {root_cert}"
            emit("error", msg, name="verify.root_cert.not_found")
            raise RuntimeError(msg)

        root_pem = root_cert.read_text()

    with open(dest, "w") as f:
        f.write(chain.read_text())
        if root_pem:
            f.write(root_pem)
            if root_cert:
                emit("detail_ok", "Root CA loaded from provided certificate",
                     name="verify.root_ca.found")
            else:
                emit("detail_ok", "Root CA found in system store",
                     name="verify.root_ca.found")
        else:
            if raise_if_not_found:
                raise RuntimeError(f"Root CA not found in system store for issuer: {root_issuer}")
            emit("warn", "Root CA not found in system store, using chain as-is",
                 name="verify.root_ca.not_found")


def parse_tsr_algo(tsr: Path) -> str | None:
    """Extract the hash algorithm from the TSR."""
    r = run(["openssl", "ts", "-reply", "-in", str(tsr), "-text"])
    for line in r.stdout.splitlines():
        if "Hash Algorithm:" in line:
            return line.split(":", 1)[1].strip().lower()
    return None


def parse_tsr_hash(tsr: Path) -> str:
    """Extract the hash embedded in the TSR from OpenSSL hexdump output."""
    import re
    r = run(["openssl", "ts", "-reply", "-in", str(tsr), "-text"])
    lines = r.stdout.splitlines()
    hex_bytes = []
    in_msg = False
    for line in lines:
        if "Message data:" in line:
            in_msg = True
            continue
        if in_msg:
            # hexdump lines: "    0000 - HH HH HH HH HH HH HH HH-HH HH ... HH   ASCII"
            # extract all hex bytes (2 hex digits) between the offset and the ASCII part
            m = re.match(r"\s+[0-9a-f]+ - ([0-9a-f .-]+)\s{3}", line)
            if not m:
                break
            hex_bytes += re.findall(r"[0-9a-f]{2}", m.group(1))
    return "".join(hex_bytes)


def print_tsr_info(tsr: Path):
    r = run(["openssl", "ts", "-reply", "-in", str(tsr), "-text"])
    for line in r.stdout.splitlines():
        if any(k in line for k in ("Time stamp:", "Hash Algorithm:")):
            emit("detail", line.strip(), name="verify.tsr_info")


TSR_INFO_KEYS = (
    "Status:", "Status info:", "Failure info:",
    "Hash Algorithm:", "Time stamp:", "Accuracy:", "Ordering:", "Nonce:",
    "TSA:", "Serial number:",
)


def tsr_info(tsr: Path, show_chain: bool = False):
    """Display TSR metadata: status, timestamp, hash algo, serial, chain."""
    r = run(["openssl", "ts", "-reply", "-in", str(tsr), "-text"])
    if r.returncode != 0:
        emit("error", f"Failed to read TSR: {r.stderr.strip()}", name="info.failed")
        return False

    for line in r.stdout.splitlines():
        if any(line.strip().startswith(k) for k in TSR_INFO_KEYS):
            emit("detail", line.strip(), name="info.field")

    tsr_hash = parse_tsr_hash(tsr)
    if tsr_hash:
        emit("detail", f"Message digest: {tsr_hash}", name="info.hash")

    # Chain info
    with tempfile.TemporaryDirectory() as tmp:
        chain_path = Path(tmp) / "chain.pem"
        try:
            extract_chain(tsr, chain_path)
        except RuntimeError:
            emit("warn", "Could not extract certificate chain", name="info.chain.failed")
            return True

        chain_text = chain_path.read_text()
        cert_count = chain_text.count("-----BEGIN CERTIFICATE-----")
        if cert_count > 1:
            emit("detail_ok", f"Full chain embedded ({cert_count} certificates)",
                 name="info.full_chain.embedded")
        else:
            emit("warn", "No full chain embedded (single certificate)",
                 name="info.full_chain.not_embedded")

        if show_chain:
            print_chain_subjects(chain_path)

    return True


def file_hash(file: Path, algo: str) -> str:
    r = run(["openssl", "dgst", f"-{algo}", "-hex", str(file)])
    return r.stdout.strip().split()[-1]


def verify(file: Path, tsr: Path, full_chain: Path) -> subprocess.CompletedProcess:
    r = run(["openssl", "ts", "-verify", "-in", str(tsr), "-data", str(file),
             "-CAfile", str(full_chain)])
    output_msg = r.stdout.strip() or r.stderr.strip()
    if r.returncode == 0:
        emit("info_ok", output_msg, name="verify.signature.result")
    else:
        emit("error", output_msg, name="verify.signature.result")
    return r

def is_verify_ok(result: subprocess.CompletedProcess | None):
    if result is None:
        return False
    return result.returncode == 0

def verify_file(file: Path, tsr: Path, algo: str | None = None, *,
                show_chain: bool = True, root_cert: Path | None = None) -> bool:
    """Full verification of a TSR against a file. Returns True if valid."""
    if algo is None:
        algo = parse_tsr_algo(tsr)
        if algo is None:
            emit("error", "Could not detect hash algorithm from TSR", name="verify.algo.failed")
            return False
        emit("detail", f"Auto-detected hash algorithm from TSR: {algo}", name="verify.algo.auto")
    
    emit("step_ok", f"Verification using hash algorithm: {algo}", name="verify.algo")
    with tempfile.TemporaryDirectory() as tmp:
        chain_path = Path(tmp) / "chain.pem"
        full_chain_path = Path(tmp) / "full_chain.pem"

        try:
            extract_chain(tsr, chain_path)
        except RuntimeError:
            return False

        # Check if full chain is embedded in the TSR
        chain_text = chain_path.read_text()
        cert_count = chain_text.count("-----BEGIN CERTIFICATE-----")
        if cert_count > 1:
            emit("step_ok", f"Full chain embedded in TSR ({cert_count} certificates)",
                 name="verify.full_chain.embedded")
        else:
            emit("warn", "TSR does not embed the full chain (single certificate)",
                 name="verify.full_chain.not_embedded")

        if show_chain:
            print_chain_subjects(chain_path)

        emit("detail", "Building verification chain...", name="verify.build_chain")
        build_full_chain(chain_path, full_chain_path, root_cert=root_cert)

        print_tsr_info(tsr)

        tsr_hash = parse_tsr_hash(tsr)
        actual_hash = file_hash(file, algo)
        match = tsr_hash == actual_hash

        emit("detail", f"Hash comparison ({algo}):", name="verify.hash")
        emit("detail", f"TSR  : {tsr_hash}", name="verify.hash.tsr")
        emit("detail", f"File : {actual_hash}", name="verify.hash.file")
        if match:
            emit("step_ok", "MATCH", name="verify.hash.match")
        else:
            emit("error", "MISMATCH", name="verify.hash.mismatch")

        emit("detail", "Verifying TSR signature...", name="verify.signature")
        r = verify(file, tsr, full_chain_path)

        sig_ok = is_verify_ok(r)
        ok = sig_ok and match
        if sig_ok:
            emit("step_ok", "Full chain verified", name="verify.full_chain.verified")
        if ok:
            emit("step_ok", f"TSR is valid for {file.name}", name="verify.ok")
        else:
            emit("error", "TSR verification failed", name="verify.failed")
        return ok


def _main():
    parser = argparse.ArgumentParser(description="Verify a RFC 3161 TSR against a file.")
    parser.add_argument("file", type=Path, help="File to verify")
    parser.add_argument("tsr", type=Path, help="TSR file (.tsr)")
    parser.add_argument("hash_algo", choices=["sha256", "sha384", "sha512"])
    args = parser.parse_args()

    if not args.file.exists():
        emit("error", f"File not found: {args.file}", name="verify.file_not_found")
        sys.exit(1)
    if not args.tsr.exists():
        emit("error", f"TSR not found: {args.tsr}", name="verify.tsr_not_found")
        sys.exit(1)

    if not verify_file(args.file, args.tsr, args.hash_algo):
        sys.exit(1)


if __name__ == "__main__":
    _main()
