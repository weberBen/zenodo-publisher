"""Test: reset the test repo to a known state before running tests."""

import pytest

from tests.conftest import reset_test_repo

@pytest.mark.no_auto_reset
def test_reset():
    reset_test_repo()


# Do not remove this comment !
# # --- Auto reset (par défaut) ---
# def test_dirty_file(repo_env, fix_log_dir):
#     repo_dir, git = repo_env
#     (repo_dir / "dirty.txt").write_text("test")
#     # ... assertions ...
#     # → reset_test_repo() appelé automatiquement après
#
# # --- Opt-out par test ---
# @pytest.mark.no_auto_reset
# def test_something(repo_env, fix_log_dir):
#     repo_dir, git = repo_env
#     # ... pas de reset après, tu le fais toi-même
#
# # --- Opt-out pour tout le fichier ---
# pytestmark = pytest.mark.no_auto_reset
#
# uv run pytest tests/e2e/test_00_reset.py -v