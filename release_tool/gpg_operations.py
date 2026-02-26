"""GPG signing operations using python-gnupg."""

from pathlib import Path

import gnupg

from . import output

def _read_gpg_conf_default_key() -> str | None:
    """
    Read the default-key directive from ~/.gnupg/gpg.conf (read-only).

    Returns:
        The default key ID, or None if not configured
    """
    gpg_conf = Path.home() / ".gnupg" / "gpg.conf"
    if not gpg_conf.exists():
        return None

    with open(gpg_conf) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and line.startswith("default-key"):
                parts = line.split(None, 1)
                if len(parts) == 2:
                    return parts[1].strip().strip('"').strip("'")
    return None


def _get_gpg_instance() -> gnupg.GPG:
    """Create a python-gnupg GPG instance."""
    return gnupg.GPG()


def get_gpg_key_info(gpg_uid: str = None) -> dict:
    """
    Query GPG for key details (read-only, no signing operation).

    Resolution order:
      1. Explicit gpg_uid if provided
      2. default-key from ~/.gnupg/gpg.conf
      3. First secret key in the keyring

    We intentionally avoid dry-run signing (gpg --dry-run --sign) to resolve
    the default key, because it may prompt the user for their passphrase if
    the keyring is locked. At this stage we only need to display key info for
    user confirmation — prompting for a passphrase would look like an actual
    signing operation (a write action) rather than a read-only lookup, which
    creates confusion.

    Args:
        gpg_uid: UID of the GPG key, or None for the default key

    Returns:
        Dict with keys: key_id, fingerprint, name, email, comment, uids

    Raises:
        RuntimeError: If GPG query fails or no key is found
    """
    gpg = _get_gpg_instance()

    # Resolve which key to use: explicit uid > gpg.conf default-key > first in keyring
    lookup_uid = gpg_uid or _read_gpg_conf_default_key()

    if lookup_uid:
        # Use keys= parameter to let gpg handle the filtering natively
        keys = gpg.list_keys(True, keys=[lookup_uid])
        if not keys:
            raise RuntimeError(f"No secret key found for '{gpg_uid}'")
    else:
        # No explicit uid and no default-key in gpg.conf: use first key
        keys = gpg.list_keys(True)
        if not keys:
            raise RuntimeError("No secret GPG keys found in keyring.")

    key = keys[0]
    uids = key.get("uids", [])
    default_uid = uids[0] if len(uids) > 0 else ""
    
    return {
        "key_id": key.get("keyid", ""),
        "fingerprint": key.get("fingerprint", ""),
        "default-uid": default_uid,
        "uids": uids,
    }


def gpg_sign_file(file_path: Path, gpg_uid: str = None, overwrite: bool = False, extra_args: list[str] = None) -> Path:
    """
    Sign a file with GPG (detached signature) using python-gnupg.

    Armor mode is controlled by --armor in extra_args (added by default via config).

    Args:
        file_path: Path to the file to sign
        gpg_uid: UID of the GPG key to use, or None to use system default
        overwrite: If True, overwrite existing signature files without prompting
        extra_args: Arguments passed to gpg (--armor included by default)

    Returns:
        Path to the signature file

    Raises:
        RuntimeError: If GPG signing fails
    """
    extra_args = extra_args or []
    armor = "--armor" in extra_args
    sig_ext = ".asc" if armor else ".sig"
    sig_path = file_path.parent / f"{file_path.name}{sig_ext}"

    if sig_path.exists() and not overwrite:
        raise RuntimeError(
            f"Signature file already exists: {sig_path}\n"
            f"Set GPG_OVERWRITE=True to overwrite."
        )

    output.detail(f"Signing {file_path.name}...")
    gpg = _get_gpg_instance()
    with open(file_path, "rb") as f:
        sig = gpg.sign_file(
            f,
            keyid=gpg_uid,
            detach=True,  # Let extra_args handle --armor
            output=str(sig_path),
            extra_args=extra_args,
        )

    # Verify the detached signature against the original file
    with open(sig_path, "rb") as f:
        verified = gpg.verify_file(f, data_filename=str(file_path))
    if not verified.valid:
        raise RuntimeError(f"GPG signing failed for {file_path.name}:\n{sig.stderr}")
    if gpg_uid and gpg_uid.lower() not in verified.fingerprint.lower():
        raise RuntimeError(
            f"Signature key mismatch for {file_path.name}: "
            f"expected '{gpg_uid}', got fingerprint '{verified.fingerprint}'"
        )

    output.detail_ok(f"{sig_path.name} created (verified: {verified.fingerprint[-16:]})")
    return sig_path


def sign_files(archived_files: list, compute_md5_fn, compute_sha256_fn, gpg_uid: str = None, overwrite: bool = False, extra_args: list[str] = None) -> list:
    """
    Sign all archived files with GPG and return signature entries.

    Signature files follow the same persist/temp rules as the files they sign.

    Args:
        archived_files: List of dicts with file_path, md5, sha256, is_preview, filename, persist, is_signature
        compute_md5_fn: Function to compute MD5 checksum of a file
        compute_sha256_fn: Function to compute SHA256 checksum of a file
        gpg_uid: UID of the GPG key to use, or None to use system default
        overwrite: If True, overwrite existing signature files without prompting
        extra_args: Arguments passed to gpg (--armor included by default)

    Returns:
        List of signature dicts
    """
    extra_args = extra_args or []
    armor = "--armor" in extra_args
    key_info = get_gpg_key_info(gpg_uid)
    fmt_label = "ASCII-armored (.asc)" if armor else "binary (.sig)"
    output.info("🔏 Signing files with GPG key:")
    output.detail(f"Key ID:  {key_info['key_id']}")
    output.detail(f"Main UID:  {key_info['default-uid']}")
    for uid in key_info['uids']:
        if uid != key_info['default-uid']:
            output.detail(f"Other UID: {uid}")
    output.detail(f"Format:  {fmt_label}")
    response = input("  Use this key? [y/n]: ").strip().lower()
    if response not in ("y", "yes", ""):
        raise RuntimeError("GPG signing aborted by user.")
    sig_ext = "asc" if armor else "sig"
    signatures = []
    for entry in archived_files:
        # Signature is created next to the signed file (same parent dir),
        # so it inherits the same temp/persist location implicitly,
        # since each file are either in archived directory or
        # tmp directory to preserve filename structure of files.
        sig_path = gpg_sign_file(entry["file_path"], gpg_uid, overwrite=overwrite, extra_args=extra_args)
        sig_md5 = compute_md5_fn(sig_path)
        sig_sha256 = compute_sha256_fn(sig_path)
        sig_filename = f"{entry['filename']}.{entry['file_path'].suffix.lstrip('.')}.{sig_ext}"
        # Carry over the persist flag from the signed file
        signatures.append({
            "file_path": sig_path,
            "md5": sig_md5,
            "sha256": sig_sha256,
            "is_preview": False,
            "filename": sig_filename,
            "persist": entry["persist"],
            "is_signature": True,
        })
    return signatures
