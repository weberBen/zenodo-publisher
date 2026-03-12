"""Test: run ZP release in a tmp git repo with minimal config, print all output."""

from pathlib import Path

from tests import conftest
from tests.utils.cli import ZpRunner
from tests.utils.git import GitClient
from tests.utils.ndjson import find_by_name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_init(directory: Path):
    """Init a minimal git repo with an initial commit."""
    git = GitClient.init(directory)
    git.add_file(".gitkeep", "")
    git.add_and_commit("init")


MINIMAL_CONFIG = {
    "project_name": {"prefix": "TestProject", "suffix": "-{tag_name}"},
    "main_branch": "main",
    "compile": {"enabled": False},
    "signing": {"sign": False},
    "hash_algorithms": ["sha256"],
    "generated_files": {
        "project": {"publishers": {"file_destination": ["zenodo"]}},
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


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_run_release(tmp_path):
    """Run ZP release in a fresh git repo and dump all output."""
    _git_init(tmp_path)

    runner = ZpRunner(tmp_path)
    result = runner.run_test(
        "release",
        config=MINIMAL_CONFIG,
        test_config={"prompts": RELEASE_PROMPTS, "verify_prompts": False},
        log_dir=conftest.log_dir,
        test_name="test_01_run",
        fail_on="ignore",
    )

    # Verify project_root matches tmp_path
    ev = find_by_name(result.events, "project_root")
    assert ev is not None, "project_root event not found"
    assert ev["data"]["project_root"] == str(tmp_path), \
        f"project_root mismatch: {ev['data']['project_root']} != {tmp_path}"
