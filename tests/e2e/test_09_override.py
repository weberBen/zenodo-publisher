"""Test: per-file config overrides for signing mode, hash algo, and env vars.

Uses the real test repo (repo_env). Tests verify that per-file sign_mode
overrides work, that sign_hash_algo changes the signed content, and that
env vars can be passed via export.
"""

import tempfile
from pathlib import Path

import gnupg
import pytest

from tests.utils.cli import ZpRunner
from tests.utils.github import GithubClient
from tests.utils.ndjson import find_by_name, find_errors
from tests.utils import fs


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TAG = "v-test-override"


def _signing_on(gpg_uid, **overrides):
    cfg = {"sign": True, "gpg": {"uid": gpg_uid}}
    cfg.update(overrides)
    return cfg


def _base_config(archive_dir: Path, **overrides) -> dict:
    config = {
        "project_name": {"prefix": "TestProject", "suffix": "-{tag_name}"},
        "main_branch": "main",
        "compile": {"enabled": False},
        "hash_algorithms": ["sha256"],
        "archive": {"format": "zip", "dir": str(archive_dir)},
        "prompt_validation_level": "danger",
    }
    config.update(overrides)
    return config


_PROMPTS = {
    "enter_tag": TAG,
    "release_title": "",
    "release_notes": "",
    "confirm_build": "yes",
    "confirm_publish": "no",
    "confirm_persist_overwrite": "yes",
    "confirm_gpg_key": "yes",
}

_TEST_CONFIG = {"prompts": _PROMPTS, "verify_prompts": False}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gpg_warmup(gpg_uid: str | None) -> None:
    """Sign a throwaway file to ensure the GPG agent has the passphrase cached."""
    if not gpg_uid:
        return
    gpg = gnupg.GPG()
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"warmup")
        warmup_path = Path(f.name)
    sig_path = warmup_path.with_suffix(".txt.asc")
    try:
        with open(warmup_path, "rb") as fh:
            gpg.sign_file(fh, keyid=gpg_uid, detach=True,
                          output=str(sig_path), extra_args=["--armor"])
    finally:
        warmup_path.unlink(missing_ok=True)
        sig_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def override_env(repo_env, fix_gpg_uid):
    """Yield (repo_dir, git, gh, archive_dir, gpg_uid)."""
    repo_dir, git = repo_env
    gh = GithubClient(repo_dir)

    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)
    git._run("tag", "-d", TAG, check=False)
    git._run("push", "origin", f":refs/tags/{TAG}", check=False)

    archive_dir = Path(tempfile.mkdtemp())

    _gpg_warmup(fix_gpg_uid)

    yield repo_dir, git, gh, archive_dir, fix_gpg_uid

    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)


def _create_pattern_file(repo_dir, git):
    """Create a pattern file (output.txt) gitignored so repo stays clean."""
    (repo_dir / "output.txt").write_text("compiled output for override test")
    gitignore = repo_dir / ".gitignore"
    existing = gitignore.read_text() if gitignore.exists() else ""
    if "output.txt" not in existing:
        gitignore.write_text(existing + "\noutput.txt\n")
        git.add_and_commit("gitignore output.txt")
        git.push()


# ---------------------------------------------------------------------------
# Tests: per-file sign_mode override
# ---------------------------------------------------------------------------

def test_per_file_sign_mode_override(override_env, fix_log_path):
    """Global sign_mode=file_hash, per-file override to file on pattern.

    Project should use file_hash (global), pattern should use file (override).
    Both should produce signatures but with different naming.
    """
    repo_dir, git, gh, archive_dir, gpg_uid = override_env
    _create_pattern_file(repo_dir, git)

    config = _base_config(
        archive_dir,
        identity_hash_algo="sha256",
        signing=_signing_on(gpg_uid, sign_mode="file_hash"),
        generated_files={
            "paper": {
                "pattern": "output.txt",
                "sign": True,
                "sign_mode": "file",  # override: sign the file directly
                "publishers": {"destination": {"file": []}},
            },
            "project": {
                "sign": True,
                # no sign_mode override: uses global file_hash
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    names = [f.name for f in files]

    # Both should have signatures
    assert "output.txt" in names
    assert "output.txt.asc" in names, f"Pattern should have .asc (file mode). Got: {names}"

    zip_files = [f for f in files if f.suffix == ".zip"]
    assert zip_files
    zip_sigs = [n for n in names if zip_files[0].name in n and n.endswith(".asc")]
    assert zip_sigs, f"Project should have .asc (file_hash mode). Got: {names}"


def test_per_file_sign_mode_reversed(override_env, fix_log_path):
    """Global sign_mode=file, per-file override to file_hash on pattern."""
    repo_dir, git, gh, archive_dir, gpg_uid = override_env
    _create_pattern_file(repo_dir, git)

    sign_hash_algo = "sha256"
    config = _base_config(
        archive_dir,
        identity_hash_algo=sign_hash_algo,
        signing=_signing_on(gpg_uid, sign_mode="file"),
        generated_files={
            "paper": {
                "pattern": "output.txt",
                "sign": True,
                "sign_mode": "file_hash",  # override: sign hash instead of file
                "publishers": {"destination": {"file": []}},
            },
            "project": {
                "sign": True,
                # no override: uses global file mode
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    names = [f.name for f in files]

    # Both should have signatures
    # file_hash mode produces {filename}.{algo}.asc, file mode produces {filename}.asc
    pattern_hash_sig_name = f"output.txt.{sign_hash_algo}.asc"  # file_hash mode (default sign_hash_algo=sha256)
    assert pattern_hash_sig_name in names, f"Pattern should have {pattern_hash_sig_name}. Got: {names}"
    zip_files = [f for f in files if f.suffix == ".zip"]
    assert zip_files
    zip_sig = [f for f in files if f.name == zip_files[0].name + ".asc"]
    assert zip_sig, f"Project should have .asc. Got: {names}"

    import subprocess

    # Project uses global sign_mode=file: gpg --verify against the file should PASS
    verify_project = subprocess.run(
        ["gpg", "--verify", str(zip_sig[0]), str(zip_files[0])],
        capture_output=True, text=True,
    )
    assert verify_project.returncode == 0, \
        f"Project (file mode): gpg verify should pass. stderr={verify_project.stderr}"

    # Pattern uses sign_mode=file_hash: gpg --verify against the file should FAIL
    # (the signature is on the hash text, not the file itself)
    pattern_file = persist_dir / "output.txt"
    pattern_sig = persist_dir / pattern_hash_sig_name
    verify_pattern = subprocess.run(
        ["gpg", "--verify", str(pattern_sig), str(pattern_file)],
        capture_output=True, text=True,
    )
    assert verify_pattern.returncode != 0, \
        f"Pattern (file_hash mode): gpg verify against file should FAIL (signed hash, not file)"

    # Cross-check: each signature must NOT verify the OTHER file
    cross1 = subprocess.run(
        ["gpg", "--verify", str(zip_sig[0]), str(pattern_file)],
        capture_output=True, text=True,
    )
    assert cross1.returncode != 0, \
        f"Project sig should NOT verify pattern file"

    cross2 = subprocess.run(
        ["gpg", "--verify", str(pattern_sig), str(zip_files[0])],
        capture_output=True, text=True,
    )
    assert cross2.returncode != 0, \
        f"Pattern sig should NOT verify project file"


def test_both_file_mode_signatures_match_own_file(override_env, fix_log_path):
    """Both files signed in file mode: each signature verifies only its own file."""
    repo_dir, git, gh, archive_dir, gpg_uid = override_env
    _create_pattern_file(repo_dir, git)

    config = _base_config(
        archive_dir,
        signing=_signing_on(gpg_uid, sign_mode="file"),
        generated_files={
            "paper": {
                "pattern": "output.txt",
                "sign": True,
                "publishers": {"destination": {"file": []}},
            },
            "project": {
                "sign": True,
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    import subprocess
    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)

    pattern_file = persist_dir / "output.txt"
    pattern_sig = persist_dir / "gpg_sign" / "output.txt.asc"
    zip_files = [f for f in files if f.suffix == ".zip"]
    zip_sig = persist_dir / "gpg_sign" / (zip_files[0].name + ".asc")

    assert pattern_file.exists() and pattern_sig.exists()
    assert zip_files and zip_sig.exists()

    # Each sig verifies its own file
    assert subprocess.run(
        ["gpg", "--verify", str(pattern_sig), str(pattern_file)],
        capture_output=True, text=True,
    ).returncode == 0, "Pattern sig should verify pattern file"

    assert subprocess.run(
        ["gpg", "--verify", str(zip_sig), str(zip_files[0])],
        capture_output=True, text=True,
    ).returncode == 0, "Project sig should verify project file"

    # Each sig does NOT verify the other file
    assert subprocess.run(
        ["gpg", "--verify", str(pattern_sig), str(zip_files[0])],
        capture_output=True, text=True,
    ).returncode != 0, "Pattern sig should NOT verify project file"

    assert subprocess.run(
        ["gpg", "--verify", str(zip_sig), str(pattern_file)],
        capture_output=True, text=True,
    ).returncode != 0, "Project sig should NOT verify pattern file"


def test_both_file_hash_mode_signatures_match_own_hash(override_env, fix_log_path):
    """Both files signed in file_hash mode: each signature verifies only its own hash file."""
    repo_dir, git, gh, archive_dir, gpg_uid = override_env
    _create_pattern_file(repo_dir, git)

    sign_hash_algo = "sha512"
    config = _base_config(
        archive_dir,
        identity_hash_algo=sign_hash_algo,
        signing=_signing_on(gpg_uid, sign_mode="file_hash"),
        generated_files={
            "paper": {
                "pattern": "output.txt",
                "sign": True,
                "publishers": {"destination": {"file": []}},
            },
            "project": {
                "sign": True,
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    import subprocess
    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)

    pattern_file = persist_dir / "output.txt"
    # file_hash mode: sig name is {filename}.{algo}.asc
    pattern_sig = persist_dir / "gpg_sign" / f"output.txt.{sign_hash_algo}.asc"
    zip_files = [f for f in files if f.suffix == ".zip"]
    zip_sig = persist_dir / "gpg_sign" / f"{zip_files[0].name}.{sign_hash_algo}.asc"

    assert pattern_file.exists(), f"Pattern file missing. Got: {[f.name for f in files]}"
    assert pattern_sig.exists(), f"Pattern sig missing. Expected {pattern_sig.name}. Got: {[f.name for f in files]}"
    assert zip_files and zip_sig.exists(), f"Zip sig missing. Expected {zip_sig.name}. Got: {[f.name for f in files]}"

    # Recreate hash files (what ZP signed: "algo:hexvalue", no newline)
    pattern_hash = fs.compute_hash(pattern_file, sign_hash_algo)
    project_hash = fs.compute_hash(zip_files[0], sign_hash_algo)

    pattern_hash_file = persist_dir / "_verify_pattern.txt"
    pattern_hash_file.write_text(f"{sign_hash_algo}:{pattern_hash}", encoding="ascii")

    project_hash_file = persist_dir / "_verify_project.txt"
    project_hash_file.write_text(f"{sign_hash_algo}:{project_hash}", encoding="ascii")

    # Each sig verifies its own hash file
    assert subprocess.run(
        ["gpg", "--verify", str(pattern_sig), str(pattern_hash_file)],
        capture_output=True, text=True,
    ).returncode == 0, "Pattern sig should verify pattern hash file"

    assert subprocess.run(
        ["gpg", "--verify", str(zip_sig), str(project_hash_file)],
        capture_output=True, text=True,
    ).returncode == 0, "Project sig should verify project hash file"

    # Each sig does NOT verify the other hash file
    assert subprocess.run(
        ["gpg", "--verify", str(pattern_sig), str(project_hash_file)],
        capture_output=True, text=True,
    ).returncode != 0, "Pattern sig should NOT verify project hash file"

    assert subprocess.run(
        ["gpg", "--verify", str(zip_sig), str(pattern_hash_file)],
        capture_output=True, text=True,
    ).returncode != 0, "Project sig should NOT verify pattern hash file"

    pattern_hash_file.unlink()
    project_hash_file.unlink()


# ---------------------------------------------------------------------------
# Tests: GPG digest algorithm via extra_args
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("digest_algo,expected_code", [
    ("SHA1", 2),
    ("SHA256", 8),
    ("SHA384", 9),
    ("SHA512", 10),
    ("RIPEMD160", 3),
])
def test_gpg_digest_algo_override(override_env, fix_log_path, digest_algo, expected_code):
    """Override GPG digest algorithm via extra_args and verify it's used in the signature."""
    repo_dir, _, _, archive_dir, gpg_uid = override_env

    config = _base_config(
        archive_dir,
        signing=_signing_on(
            gpg_uid, sign_mode="file",
            gpg={"uid": gpg_uid, "extra_args": ["--digest-algo", digest_algo]},
        ),
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors with --digest-algo {digest_algo}: {errors}"

    persist_dir = archive_dir / TAG
    sigs = [f for f in fs.list_files(persist_dir) if f.name.endswith(".asc")]
    assert sigs, f"No .asc signature found"

    # Check the digest algo used in the signature via gpg --list-packets
    import subprocess
    packets = subprocess.run(
        ["gpg", "--list-packets", str(sigs[0])],
        capture_output=True, text=True,
    )
    assert packets.returncode == 0, f"gpg --list-packets failed: {packets.stderr}"

    # Look for "digest algo X" in the output
    assert f"digest algo {expected_code}" in packets.stdout, \
        f"Expected digest algo {expected_code} ({digest_algo}) in signature. " \
        f"gpg output: {packets.stdout}"


def test_per_file_mixed_sign_on_off(override_env, fix_log_path):
    """Global sign=true, but pattern sign=false: only project signed."""
    repo_dir, git, gh, archive_dir, gpg_uid = override_env
    _create_pattern_file(repo_dir, git)

    config = _base_config(
        archive_dir,
        signing=_signing_on(gpg_uid, sign_mode="file"),
        generated_files={
            "paper": {
                "pattern": "output.txt",
                "sign": False,  # explicitly disabled
                "publishers": {"destination": {"file": []}},
            },
            "project": {
                # inherits global sign=true
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    names = [f.name for f in files]

    # Pattern should NOT be signed
    assert "output.txt" in names
    assert "output.txt.asc" not in names, f"Pattern should NOT have .asc. Got: {names}"

    # Project should be signed
    zip_files = [f for f in files if f.suffix == ".zip"]
    assert zip_files
    zip_sigs = [n for n in names if zip_files[0].name in n and n.endswith(".asc")]
    assert zip_sigs, f"Project should have .asc. Got: {names}"


# ---------------------------------------------------------------------------
# Tests: sign_hash_algo verification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sign_hash_algo", ["md5", "sha1", "sha256", "sha512"])
def test_file_hash_mode_signed_content_matches(override_env, fix_log_path, sign_hash_algo):
    """In file_hash mode, GPG signs 'algo:hexvalue'. Verify the hex matches the actual file.

    sign_hash_algo controls which hash of the file is written to the
    intermediate text file before GPG signs it. This test computes the
    hash locally and checks it appears in the file_hashes event.
    """
    repo_dir, git, gh, archive_dir, gpg_uid = override_env

    # Cleanup in case of previous run
    if gh.has_release(TAG):
        gh.delete_release(TAG, cleanup_tag=True)
    git._run("tag", "-d", TAG, check=False)
    git._run("push", "origin", f":refs/tags/{TAG}", check=False)

    run_dir = Path(tempfile.mkdtemp())
    config = _base_config(
        run_dir,
        identity_hash_algo=sign_hash_algo,
        signing=_signing_on(gpg_uid, sign_mode="file_hash"),
        hash_algorithms=["md5", "sha1", "sha512"],
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Errors with sign_hash_algo={sign_hash_algo}: {errors}"

    persist_dir = run_dir / TAG
    files = fs.list_files(persist_dir)

    # Find the archive file
    zip_files = [f for f in files if f.suffix == ".zip"]
    assert zip_files, f"No .zip found"

    # Compute the hash locally with the same algo
    local_hash = fs.compute_hash(zip_files[0], sign_hash_algo)

    # Verify the file_hashes event contains the correct hash
    from tests.utils.ndjson import find_data
    file_hashes = find_data(result.events, "file_hashes")
    assert file_hashes, "No file_hashes data event"

    reported = file_hashes.get(zip_files[0].name, {})
    assert sign_hash_algo in reported, \
        f"{sign_hash_algo} not in reported hashes: {reported}"
    assert reported[sign_hash_algo] == local_hash, \
        f"{sign_hash_algo} mismatch: reported={reported[sign_hash_algo]}, local={local_hash}"

    # Signature should exist
    sigs = [f for f in files if f.name.endswith(".asc")]
    assert sigs, f"No .asc signature found"

    # Recreate the hash file that ZP signed: "algo:hexvalue" (no newline)
    import subprocess
    formatted_hash = f"{sign_hash_algo}:{local_hash}"
    hash_file = persist_dir / f"_verify_{sign_hash_algo}.txt"
    hash_file.write_text(formatted_hash, encoding="ascii")

    # Verify the signature against the recreated hash file
    verify = subprocess.run(
        ["gpg", "--verify", str(sigs[0]), str(hash_file)],
        capture_output=True, text=True,
    )
    assert verify.returncode == 0, \
        f"GPG verification failed for {sign_hash_algo}: {verify.stderr}"

    hash_file.unlink()


def test_file_mode_signature_verifiable(override_env, fix_log_path):
    """In file mode, GPG signs the actual file. Verify signature exists and file is intact."""
    repo_dir, git, gh, archive_dir, gpg_uid = override_env

    config = _base_config(
        archive_dir,
        signing=_signing_on(gpg_uid, sign_mode="file"),
        generated_files={
            "project": {"publishers": {"destination": {"file": []}}},
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)

    zip_files = [f for f in files if f.suffix == ".zip"]
    assert zip_files

    sig_files = [f for f in files if f.name.endswith(".asc")]
    assert sig_files, f"No .asc signature"

    # The .asc file should reference the archive name
    assert zip_files[0].name in sig_files[0].name, \
        f"Signature name should contain archive name. sig={sig_files[0].name}, archive={zip_files[0].name}"

    # Verify file hash matches event
    from tests.utils.ndjson import find_data
    file_hashes = find_data(result.events, "file_hashes")
    local_hash = fs.compute_hash(zip_files[0], "sha256")
    assert file_hashes[zip_files[0].name]["sha256"] == local_hash

    # Verify GPG signature against the actual file
    import subprocess
    verify = subprocess.run(
        ["gpg", "--verify", str(sig_files[0]), str(zip_files[0])],
        capture_output=True, text=True,
    )
    assert verify.returncode == 0, \
        f"GPG verification failed: {verify.stderr}"


# ---------------------------------------------------------------------------
# Tests: env var override via export
# ---------------------------------------------------------------------------
# Tests: env var override
# ---------------------------------------------------------------------------

def test_env_var_override_fake_token(override_env, fix_log_path):
    """Passing a fake ZENODO_TOKEN via os.environ should override .zenodo.env and cause auth failure."""
    repo_dir, _, _, archive_dir, _ = override_env

    prompts_publish = {**_PROMPTS, "confirm_publish": "yes"}
    config = _base_config(
        archive_dir,
        signing={"sign": False},
        zenodo={
            "api_url": "https://sandbox.zenodo.org/api",
            "concept_doi": "432538",
        },
        generated_files={
            "project": {
                "publishers": {"destination": {"file": ["zenodo"]}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config={"prompts": prompts_publish, "verify_prompts": False},
                             log_path=fix_log_path,
                             fail_on="ignore",
                             env={"ZENODO_TOKEN": "fake_invalid_token_12345"})

    errors = find_errors(result.events)
    assert errors, f"Expected Zenodo error with fake token. events={result.events}"
    assert any("zenodo_operations" in e.get("pipe", "") for e in errors), \
        f"Expected error from zenodo_operations. Got: {errors}"


# ---------------------------------------------------------------------------
# Tests: per-file rename override
# ---------------------------------------------------------------------------

def test_pattern_rename(override_env, fix_log_path):
    """Pattern with rename=true: file should be renamed to ProjectName-tag.ext."""
    repo_dir, git, gh, archive_dir, gpg_uid = override_env
    _create_pattern_file(repo_dir, git)

    config = _base_config(
        archive_dir,
        signing={"sign": False},
        generated_files={
            "paper": {
                "pattern": "output.txt",
                "rename": True,
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    names = [f.name for f in files]

    expected = f"TestProject-{TAG}.txt"
    assert expected in names, \
        f"Expected renamed file '{expected}'. Got: {names}"
    assert "output.txt" not in names, \
        f"Original name should not be present when rename=true. Got: {names}"


def test_pattern_no_rename(override_env, fix_log_path):
    """Pattern with rename=false (default): file keeps original name."""
    repo_dir, git, gh, archive_dir, gpg_uid = override_env
    _create_pattern_file(repo_dir, git)

    config = _base_config(
        archive_dir,
        signing={"sign": False},
        generated_files={
            "paper": {
                "pattern": "output.txt",
                # rename defaults to false
                "publishers": {"destination": {"file": []}},
            },
        },
    )

    runner = ZpRunner(repo_dir)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")

    errors = find_errors(result.events)
    assert not errors, f"Unexpected errors: {errors}"

    persist_dir = archive_dir / TAG
    files = fs.list_files(persist_dir)
    names = [f.name for f in files]

    assert "output.txt" in names, f"Original name should be kept. Got: {names}"
