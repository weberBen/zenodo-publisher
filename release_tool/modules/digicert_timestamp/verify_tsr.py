#!/usr/bin/env python3
"""Verify a RFC 3161 timestamp response (TSR) against a file."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd: list[str], capture=True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=capture, text=True)


def extract_chain(tsr: Path, dest: Path):
    p1 = subprocess.Popen(
        ["openssl", "ts", "-reply", "-in", str(tsr), "-token_out"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    p2 = subprocess.Popen(
        ["openssl", "pkcs7", "-inform", "DER", "-print_certs", "-out", str(dest)],
        stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    p1.stdout.close()
    _, err = p2.communicate()
    p1.wait()
    if p2.returncode != 0:
        sys.exit(f"Failed to extract certificate chain from TSR: {err.decode()}")


def print_chain_subjects(chain: Path):
    r = run(["openssl", "crl2pkcs7", "-nocrl", "-certfile", str(chain)])
    r2 = subprocess.run(
        ["openssl", "pkcs7", "-print_certs", "-noout"],
        input=r.stdout, capture_output=True, text=True,
    )
    for line in r2.stdout.splitlines():
        if line.startswith(("subject=", "issuer=")):
            print(f"    {line}")


def get_root_issuer(chain: Path) -> str:
    r = run(["openssl", "crl2pkcs7", "-nocrl", "-certfile", str(chain)])
    r2 = subprocess.run(
        ["openssl", "pkcs7", "-print_certs", "-noout"],
        input=r.stdout, capture_output=True, text=True,
    )
    issuers = [l for l in r2.stdout.splitlines() if l.startswith("issuer=")]
    return issuers[-1].removeprefix("issuer=").strip() if issuers else ""


def build_full_chain(chain: Path, dest: Path):
    certs_dir = Path("/etc/ssl/certs")
    root_issuer = get_root_issuer(chain)
    print(f"    Looking for root CA: {root_issuer}")

    root_pem = None
    for cert_file in certs_dir.glob("*.pem"):
        r = run(["openssl", "x509", "-in", str(cert_file), "-noout", "-subject"])
        if r.returncode == 0 and root_issuer in r.stdout:
            root_pem = cert_file.read_text()
            break

    with open(dest, "w") as f:
        f.write(chain.read_text())
        if root_pem:
            f.write(root_pem)
            print("    Root CA found in system store.")
        else:
            print("    Root CA not found in system store, using chain as-is.")


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
            print(f"    {line.strip()}")


def file_hash(file: Path, algo: str) -> str:
    r = run(["openssl", "dgst", f"-{algo}", "-hex", str(file)])
    return r.stdout.strip().split()[-1]


def verify(file: Path, tsr: Path, full_chain: Path) -> bool:
    r = run(["openssl", "ts", "-verify", "-in", str(tsr), "-data", str(file),
             "-CAfile", str(full_chain)])
    print(r.stdout.strip() or r.stderr.strip())
    return r.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="Verify a RFC 3161 TSR against a file.")
    parser.add_argument("file", type=Path, help="File to verify")
    parser.add_argument("tsr", type=Path, help="TSR file (.tsr)")
    parser.add_argument("hash_algo", choices=["sha256", "sha384", "sha512"])
    args = parser.parse_args()

    if not args.file.exists():
        sys.exit(f"ERROR: file not found: {args.file}")
    if not args.tsr.exists():
        sys.exit(f"ERROR: TSR not found: {args.tsr}")

    with tempfile.TemporaryDirectory() as tmp:
        chain = Path(tmp) / "chain.pem"
        full_chain = Path(tmp) / "full_chain.pem"

        print("==> Extracting certificate chain from TSR...")
        extract_chain(args.tsr, chain)

        print("\n==> Chain certificates:")
        print_chain_subjects(chain)

        print("\n==> Building full chain...")
        build_full_chain(chain, full_chain)

        print("\n==> TSR info:")
        print_tsr_info(args.tsr)

        tsr_hash = parse_tsr_hash(args.tsr)
        actual_hash = file_hash(args.file, args.hash_algo)
        match = "✓ match" if tsr_hash == actual_hash else "✗ MISMATCH"

        print(f"\n==> Hash comparison ({args.hash_algo}):")
        print(f"    TSR  : {tsr_hash}")
        print(f"    File : {actual_hash}")
        print(f"    {match}")

        print("\n==> Verifying TSR signature...")
        ok = verify(args.file, args.tsr, full_chain)

        print()
        if ok:
            print(f"RESULT: OK — TSR is valid for {args.file.name}")
        else:
            print(f"RESULT: FAILED — TSR verification failed")
            sys.exit(1)


if __name__ == "__main__":
    main()
