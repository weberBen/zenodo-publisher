"""GPG signing operations using python-gnupg."""

from pathlib import Path

import gnupg

from . import output
from . import prompts
from .errors import GpgError

def _read_gpg_conf_default_key() -> str | None:
    """Read the default-key directive from ~/.gnupg/gpg.conf (read-only)."""
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
    # python-gnupg uses a logger named "gnupg" internally to log gpg command
    # lines and status messages (see https://gnupg.readthedocs.io/en/stable/).
    # By attaching our handler previously defined, these messages appear in --debug output without
    # any explicit logging calls in gpg_operations.py.
    return gnupg.GPG()


def get_gpg_key_info(gpg_uid: str = None) -> dict:
    """Query GPG for key details (read-only, no signing operation).

    We intentionally avoid dry-run signing (gpg --dry-run --sign) to resolve
    the default key, because it may prompt the user for their passphrase.
    At this stage we only need to display key info for confirmation.

    Resolution order:
      1. Explicit gpg_uid if provided
      2. default-key from ~/.gnupg/gpg.conf
      3. First secret key in the keyring
    """
    gpg = _get_gpg_instance()
    lookup_uid = gpg_uid or _read_gpg_conf_default_key()

    if lookup_uid:
        keys = gpg.list_keys(True, keys=[lookup_uid])
        if not keys:
            raise GpgError(f"No secret key found for '{gpg_uid}'", name="no_secret_key")
    else:
        keys = gpg.list_keys(True)
        if not keys:
            raise GpgError("No secret GPG keys found in keyring.", name="no_keys")

    key = keys[0]
    uids = key.get("uids", [])
    default_uid = uids[0] if len(uids) > 0 else ""

    return {
        "key_id": key.get("keyid", ""),
        "fingerprint": key.get("fingerprint", ""),
        "default-uid": default_uid,
        "uids": uids,
    }


def gpg_sign_file(file_path: Path, output_dir: Path, gpg_uid: str = None,
                   overwrite: bool = False, extra_args: list[str] = None) -> Path:
    """Sign a file with GPG (detached signature).

    Returns path to the signature file.
    """
    extra_args = extra_args or []
    armor = "--armor" in extra_args
    sig_ext = ".asc" if armor else ".sig"
    sig_path = output_dir / f"{file_path.name}{sig_ext}"

    if sig_path.exists() and not overwrite:
        raise GpgError(
            f"Signature file already exists: {sig_path}\n"
            f"Use overwrite option to replace.",
            name="sig_exists",
        )

    output.detail("Signing {filename}...", filename=file_path.name, name="signing")
    gpg = _get_gpg_instance()
    with open(file_path, "rb") as f:
        sig = gpg.sign_file(
            f,
            keyid=gpg_uid,
            detach=True,
            output=str(sig_path),
            extra_args=extra_args,
        )

    if not sig_path.exists():
        raise GpgError(
            f"GPG produced no signature for {file_path.name}. "
            f"Status: {getattr(sig, 'status', 'unknown')}. "
            f"stderr: {getattr(sig, 'stderr', '').strip() or '(empty)'}. "
            f"Check that the GPG key is unlocked (agent has the passphrase).",
            name="sign_no_output",
        )

    # Verify the detached signature against the original file
    with open(sig_path, "rb") as f:
        verified = gpg.verify_file(f, data_filename=str(file_path))
    if not verified.valid:
        raise GpgError(f"GPG signing failed for {file_path.name}:\n{sig.stderr}", name="sign_failed")
    if gpg_uid and gpg_uid.lower() not in verified.fingerprint.lower():
        raise GpgError(
            f"Signature key mismatch for {file_path.name}: "
            f"expected '{gpg_uid}', got fingerprint '{verified.fingerprint}'",
            name="key_mismatch",
        )

    output.detail_ok("{sig_name} created (verified: {short_fingerprint})",
                     sig_name=sig_path.name, short_fingerprint=verified.fingerprint[-16:],
                     ingerprint=verified.fingerprint, name="signed")
    return sig_path


def prompt_gpg_key(gpg_uid: str | None, extra_args: list[str]) -> None:
    """Display GPG key info and prompt user for confirmation."""
    armor = "--armor" in extra_args
    key_info = get_gpg_key_info(gpg_uid)
    fmt_label = "ASCII-armored (.asc)" if armor else "binary (.sig)"

    output.info("🔏 Signing files with GPG key:")
    output.detail("Key ID:  {key_id}", key_id=key_info['key_id'], name="key_id")
    output.detail("Main UID:  {uid}", uid=key_info['default-uid'], name="main_uid")

    for uid in key_info['uids']:
        if uid != key_info['default-uid']:
            output.detail("Other UID: {uid}", uid=uid, name="other_uid")
    output.detail("Format:  {fmt}", fmt=fmt_label, name="format")

    if not prompts.confirm_gpg_key.ask("Use this key?").is_accept:
        raise GpgError("GPG signing aborted by user.", name="aborted")
