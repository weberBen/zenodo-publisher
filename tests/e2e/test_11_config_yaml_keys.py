"""Test: validation statique des clés YAML inconnues dans .zp.yaml.

Vérifie que toute clé (ou chemin de clé) absente des définitions ConfigOption
lève immédiatement config.yaml.unknown_key, à chaque niveau de profondeur.
"""

from pathlib import Path

from tests.utils.cli import ZpRunner
from tests.utils.git import GitClient
from tests.utils.ndjson import find_errors, find_by_name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_init(directory: Path):
    git = GitClient.init(directory)
    git.add_file(".gitkeep", "")
    git.add_and_commit("init")


def _assert_unknown_key_error(result, key_path: str):
    """Assert config.yaml.unknown_key est présent et mentionne key_path dans le message."""
    error = find_by_name(result.events, "config_error.loading.config.yaml.unknown_key")
    assert error, \
        f"Expected config.yaml.unknown_key for '{key_path}'. Events: {result.events}"
    assert key_path in error.get("msg", ""), \
        f"Expected '{key_path}' in error message. Got: {error.get('msg')}"


BASE_CONFIG = {
    "main_branch": "main",
    "compile": {"enabled": False},
    "signing": {"sign": False},
    "prompt_validation_level": "danger",
}

RELEASE_PROMPTS = {
    "enter_tag": "v1.0.0",
    "release_title": "",
    "release_notes": "",
    "confirm_build": "yes",
    "confirm_publish": "no",
}

_TEST_CONFIG = {"prompts": RELEASE_PROMPTS, "verify_prompts": False}


# ---------------------------------------------------------------------------
# Tests: clés inconnues à différentes profondeurs
# ---------------------------------------------------------------------------

def test_unknown_top_level_key(tmp_path, fix_log_path):
    """Clé inconnue à la racine (depth 1) → config.yaml.unknown_key."""
    _git_init(tmp_path)
    config = {**BASE_CONFIG, "typo_key": "value"}
    result = ZpRunner(tmp_path).run_test("release", config=config,
                                         test_config=_TEST_CONFIG,
                                         log_path=fix_log_path,
                                         fail_on="ignore")
    _assert_unknown_key_error(result, "typo_key")


def test_unknown_nested_key(tmp_path, fix_log_path):
    """Sous-clé inconnue dans section connue (depth 2) → config.yaml.unknown_key."""
    _git_init(tmp_path)
    config = {**BASE_CONFIG, "compile": {"enabled": False, "typo_sub_key": "value"}}
    result = ZpRunner(tmp_path).run_test("release", config=config,
                                         test_config=_TEST_CONFIG,
                                         log_path=fix_log_path,
                                         fail_on="ignore")
    _assert_unknown_key_error(result, "compile.typo_sub_key")


def test_unknown_deep_nested_key(tmp_path, fix_log_path):
    """Sous-clé inconnue à depth 3 (signing.gpg.unknown) → config.yaml.unknown_key."""
    _git_init(tmp_path)
    config = {
        **BASE_CONFIG,
        "signing": {"sign": False, "gpg": {"uid": None, "typo_gpg_key": "value"}},
    }
    result = ZpRunner(tmp_path).run_test("release", config=config,
                                         test_config=_TEST_CONFIG,
                                         log_path=fix_log_path,
                                         fail_on="ignore")
    _assert_unknown_key_error(result, "signing.gpg.typo_gpg_key")


def test_unknown_key_in_archive_section(tmp_path, fix_log_path):
    """Sous-clé inconnue dans archive: → config.yaml.unknown_key."""
    _git_init(tmp_path)
    config = {**BASE_CONFIG, "archive": {"format": "zip", "typo_archive_key": "value"}}
    result = ZpRunner(tmp_path).run_test("release", config=config,
                                         test_config=_TEST_CONFIG,
                                         log_path=fix_log_path,
                                         fail_on="ignore")
    _assert_unknown_key_error(result, "archive.typo_archive_key")


# ---------------------------------------------------------------------------
# Tests: sections opaques — pas d'erreur sur les sous-clés
# ---------------------------------------------------------------------------

def test_opaque_section_generated_files_no_error(tmp_path, fix_log_path):
    """generated_files est opaque : ses sous-clés ne déclenchent pas yaml.unknown_key."""
    _git_init(tmp_path)
    config = {
        **BASE_CONFIG,
        "generated_files": {
            "project": {"publishers": {"destination": {"file": []}}},
        },
    }
    result = ZpRunner(tmp_path).run_test("release", config=config,
                                         test_config=_TEST_CONFIG,
                                         log_path=fix_log_path,
                                         fail_on="ignore")
    assert not find_by_name(result.events, "config_error.loading.config.yaml.unknown_key"), \
        "No yaml.unknown_key expected for generated_files (opaque section)"


def test_opaque_section_modules_no_error(tmp_path, fix_log_path):
    """modules: est opaque : ses sous-clés ne déclenchent pas yaml.unknown_key."""
    _git_init(tmp_path)
    config = {**BASE_CONFIG, "modules": {"my_module": {"some_key": "value"}}}
    result = ZpRunner(tmp_path).run_test("release", config=config,
                                         test_config=_TEST_CONFIG,
                                         log_path=fix_log_path,
                                         fail_on="ignore")
    assert not find_by_name(result.events, "config_error.loading.config.yaml.unknown_key"), \
        "No yaml.unknown_key expected for modules (opaque section)"
