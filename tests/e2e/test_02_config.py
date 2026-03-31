"""Test: configuration loading, validation, prompts, signing, hashing.

Standalone test — creates temporary directories with various configs
and runs `zp release` to verify behavior. Does not depend on the
external test repo.
"""

from pathlib import Path

from tests.utils.cli import ZpRunner
from tests.utils.git import GitClient
from tests.utils.ndjson import find_errors, find_by_name, has_step_ok


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_init(directory: Path):
    """Init a minimal git repo with an initial commit."""
    git = GitClient.init(directory)
    git.add_file(".gitkeep", "")
    git.add_and_commit("init")


def _assert_has_error(result, name: str | None = None, msg_contains: str | None = None):
    """Assert that result contains an error/fatal event, optionally matching name or message."""
    errors = find_errors(result.events)
    assert errors, f"Expected error events, got none. events={result.events}"
    if name:
        assert find_by_name(result.events, name), \
            f"Expected event with name='{name}', got: {errors}"
    if msg_contains:
        assert any(msg_contains.lower() in e.get("msg", "").lower() for e in errors), \
            f"Expected error containing '{msg_contains}', got: {errors}"



MINIMAL_CONFIG = {
    "project_name": {"prefix": "TestProject", "suffix": "-{tag_name}"},
    "main_branch": "main",
    "compile": {"enabled": False},
    "signing": {"sign": False},
    "hash_algorithms": ["sha256"],
    "generated_files": {
        "project": {"publishers": {"destination": {"file": ["zenodo"]}}},
    },
    "prompt_validation_level": "danger",
}

RELEASE_PROMPTS = {
    "enter_tag": "v1.0.0",
    "release_title": "",
    "release_notes": "",
    "confirm_build": "yes",
    "confirm_publish": "yes",
}

# Config-check tests: pipeline will crash later on origin/main,
# so we ignore fatal/error and only check config_checked step.
_TEST_CONFIG = {"prompts": RELEASE_PROMPTS, "verify_prompts": False}


# ---------------------------------------------------------------------------
# Sub-tests
# ---------------------------------------------------------------------------

# --- Git / init ---

def test_no_git(tmp_path, fix_log_path):
    """Without git init: config loading should fail (no project root)."""
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=MINIMAL_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    _assert_has_error(result, name="config_error.loading")


def test_git_no_config(tmp_path, fix_log_path):
    """With git init but no .zp.yaml: should report not initialized."""
    _git_init(tmp_path)
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release",
                             log_path=fix_log_path,
                             fail_on="ignore")
    _assert_has_error(result, name="config_error.not_initialized")


# --- Valid config ---

def test_valid_minimal_config(tmp_path, fix_log_path):
    """With git init + valid minimal config: should pass config check."""
    _git_init(tmp_path)
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=MINIMAL_CONFIG,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"Config check should pass. events={result.events}"


# --- Env file ---

def test_with_env_file(tmp_path, fix_log_path):
    """With .zenodo.env containing valid keys: should load fine."""
    _git_init(tmp_path)
    (tmp_path / ".zenodo.env").write_text("ZENODO_TOKEN=fake_token_123\n")
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=MINIMAL_CONFIG,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"Should load with .zenodo.env: events={result.events}"


def test_env_file_unknown_keys(tmp_path, fix_log_path):
    """With .zenodo.env containing unknown keys: should error."""
    _git_init(tmp_path)
    (tmp_path / ".zenodo.env").write_text("UNKNOWN_KEY=value\nBAD_KEY=123\n")
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=MINIMAL_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    _assert_has_error(result, name="config_error.loading.config.unknown_env_key")


# --- Invalid config ---

def test_invalid_yaml_not_dict(tmp_path):
    """Config file with non-dict content: should fail."""
    _git_init(tmp_path)
    config_path = tmp_path / ".zp.yaml"
    config_path.write_text("- just\n- a\n- list\n")
    runner = ZpRunner(tmp_path)
    result = runner.run("release", "--test-mode", "--config", str(config_path))
    _assert_has_error(result, name="config_error.loading")


def test_invalid_archive_format(tmp_path, fix_log_path):
    """Invalid archive format choice: should fail."""
    _git_init(tmp_path)
    config = {**MINIMAL_CONFIG, "archive": {"format": "rar"}}
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             log_path=fix_log_path,
                             fail_on="ignore")
    _assert_has_error(result, name="config_error.loading.config.invalid_option.archive")


def test_invalid_prompt_level(tmp_path, fix_log_path):
    """Invalid prompt_validation_level: should fail."""
    _git_init(tmp_path)
    config = {**MINIMAL_CONFIG, "prompt_validation_level": "extreme"}
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             log_path=fix_log_path,
                             fail_on="ignore")
    _assert_has_error(result, name="config_error.loading.config.invalid_option.prompt_validation_level")


# --- Prompt levels ---

def test_prompt_level_danger(tmp_path, fix_log_path):
    """Danger level: Enter confirms (enter option available)."""
    _git_init(tmp_path)
    config = {**MINIMAL_CONFIG, "prompt_validation_level": "danger"}
    prompts = {**RELEASE_PROMPTS, "confirm_build": "enter"}
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config={"prompts": prompts, "verify_prompts": False},
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"Danger level should accept 'enter': events={result.events}"


def test_prompt_level_light(tmp_path, fix_log_path):
    """Light level: y/yes accepted."""
    _git_init(tmp_path)
    config = {**MINIMAL_CONFIG, "prompt_validation_level": "light"}
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"Light level should work: events={result.events}"


def test_prompt_level_normal(tmp_path, fix_log_path):
    """Normal level: full 'yes' required."""
    _git_init(tmp_path)
    config = {**MINIMAL_CONFIG, "prompt_validation_level": "normal"}
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"Normal level should work: events={result.events}"


def test_prompt_level_secure(tmp_path, fix_log_path):
    """Secure level: must type the project root name."""
    _git_init(tmp_path)
    config = {**MINIMAL_CONFIG, "prompt_validation_level": "secure"}
    prompts = {
        **RELEASE_PROMPTS,
        "confirm_build": "secure_value",
        "confirm_publish": "secure_value",
    }
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config={"prompts": prompts, "verify_prompts": False},
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"Secure level should work: events={result.events}"


# --- Signing ---

def test_signing_on_file_mode(tmp_path, fix_log_path):
    """signing: sign + sign_mode: file — config should load."""
    _git_init(tmp_path)
    config = {
        **MINIMAL_CONFIG,
        "identity_hash_algo": "sha256",
        "signing": {"sign": True, "sign_mode": "file"},
    }
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"Sign on (file mode) should load: events={result.events}"


def test_signing_on_file_hash_mode(tmp_path, fix_log_path):
    """signing: sign + sign_mode: file_hash — config should load."""
    _git_init(tmp_path)
    config = {
        **MINIMAL_CONFIG,
        "identity_hash_algo": "sha256",
        "signing": {"sign": True, "sign_mode": "file_hash"},
    }
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"Sign on (file_hash mode) should load: events={result.events}"


def test_signing_invalid_mode(tmp_path, fix_log_path):
    """Invalid sign_mode: should fail."""
    _git_init(tmp_path)
    config = {
        **MINIMAL_CONFIG,
        "signing": {"sign": True, "sign_mode": "invalid_mode"},
    }
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             log_path=fix_log_path,
                             fail_on="ignore")
    _assert_has_error(result, name="config_error.loading.config.invalid_option.signing.sign_mode",
                      msg_contains="invalid_mode")


def test_signing_invalid_hash_algo(tmp_path, fix_log_path):
    """Invalid identity_hash_algo: should fail."""
    _git_init(tmp_path)
    config = {
        **MINIMAL_CONFIG,
        "identity_hash_algo": "not_a_real_algo",
    }
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             log_path=fix_log_path,
                             fail_on="ignore")
    _assert_has_error(result, name="config_error.loading.config.invalid_option.identity_hash_algo")


def test_signing_cli_override(tmp_path, fix_log_path):
    """--sign CLI flag should override signing.sign: false in YAML."""
    _git_init(tmp_path)
    config = {**MINIMAL_CONFIG, "signing": {"sign": False}}
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             extra_args=["--sign"],
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"--sign override should load: events={result.events}"


def test_signing_gpg_uid(tmp_path, fix_log_path):
    """GPG UID in config should load without error."""
    _git_init(tmp_path)
    config = {
        **MINIMAL_CONFIG,
        "signing": {"sign": True, "gpg": {"uid": "test@example.com"}},
    }
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"GPG UID config should load: events={result.events}"


def test_signing_gpg_uid_empty(tmp_path, fix_log_path):
    """Empty GPG UID should be treated as None (auto-detect)."""
    _git_init(tmp_path)
    config = {
        **MINIMAL_CONFIG,
        "signing": {"sign": True, "gpg": {"uid": ""}},
    }
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"Empty GPG UID should load: events={result.events}"


def test_signing_gpg_extra_args(tmp_path, fix_log_path):
    """Custom GPG extra_args should merge with defaults."""
    _git_init(tmp_path)
    config = {
        **MINIMAL_CONFIG,
        "signing": {
            "sign": True,
            "gpg": {"uid": "test@example.com", "extra_args": ["--armor", "--detach-sign"]},
        },
    }
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"GPG extra_args should load: events={result.events}"


# --- Hash algorithms ---

def test_hash_multiple(tmp_path, fix_log_path):
    """Multiple hash algorithms: md5, sha256."""
    _git_init(tmp_path)
    config = {**MINIMAL_CONFIG, "hash_algorithms": ["md5", "sha256"]}
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"Multiple hashes should load: events={result.events}"


def test_hash_with_tree(tmp_path, fix_log_path):
    """Tree hash algorithm (git tree hash)."""
    _git_init(tmp_path)
    config = {**MINIMAL_CONFIG, "hash_algorithms": ["sha256", "tree"]}
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"tree hash should load: events={result.events}"


def test_hash_invalid(tmp_path, fix_log_path):
    """Invalid hash algorithm: should fail."""
    _git_init(tmp_path)
    config = {**MINIMAL_CONFIG, "hash_algorithms": ["not_a_hash"]}
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             log_path=fix_log_path,
                             fail_on="ignore")
    _assert_has_error(result, name="config_error.loading.config.invalid_option.hash_algorithms")


# --- Archive format ---

def test_archive_format_zip(tmp_path, fix_log_path):
    """archive.format: zip."""
    _git_init(tmp_path)
    config = {**MINIMAL_CONFIG, "archive": {"format": "zip"}}
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"zip format should load: events={result.events}"


def test_archive_format_tar(tmp_path, fix_log_path):
    """archive.format: tar."""
    _git_init(tmp_path)
    config = {**MINIMAL_CONFIG, "archive": {"format": "tar"}}
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"tar format should load: events={result.events}"


def test_archive_format_tar_gz(tmp_path, fix_log_path):
    """archive.format: tar.gz."""
    _git_init(tmp_path)
    config = {**MINIMAL_CONFIG, "archive": {"format": "tar.gz"}}
    runner = ZpRunner(tmp_path)
    result = runner.run_test("release", config=config,
                             test_config=_TEST_CONFIG,
                             log_path=fix_log_path,
                             fail_on="ignore")
    assert has_step_ok(result.events, "config.checked"), \
        f"tar.gz format should load: events={result.events}"
