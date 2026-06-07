"""Test: reset the test repo to a known state before running tests."""

import pytest

from tests.conftest import reset_test_repo
from tests.utils.git import TEMPLATE_TAG_PREFIX

@pytest.mark.no_auto_reset
@pytest.mark.parametrize("args,desc", [
    (("tag", f"{TEMPLATE_TAG_PREFIX}test"),                                      "lightweight tag"),
    (("tag", "-a", f"{TEMPLATE_TAG_PREFIX}test", "-m", "msg"),                  "annotated tag"),
    (("push", "origin", f"{TEMPLATE_TAG_PREFIX}test"),                           "push by name"),
    (("push", "origin", f"refs/tags/{TEMPLATE_TAG_PREFIX}test"),                 "push via refs/tags/"),
    (("push", "origin", f"HEAD:refs/tags/{TEMPLATE_TAG_PREFIX}test"),            "push via refspec"),
    (("fetch", "origin", f"refs/tags/{TEMPLATE_TAG_PREFIX}test:refs/tags/{TEMPLATE_TAG_PREFIX}test"), "fetch explicit refspec"),
])
def test_template_tag_creation_forbidden(repo_env, desc, args):
    """GitClient._run doit lever PermissionError pour toute création de tag template_*."""
    _, git = repo_env
    with pytest.raises(PermissionError, match=TEMPLATE_TAG_PREFIX):
        git._run(*args)


@pytest.mark.no_auto_reset
@pytest.mark.parametrize("args,desc", [
    (("fast-import",),                    "fast-import"),
    (("bundle", "unbundle", "file.bundle"), "bundle unbundle"),
])
def test_forbidden_commands(repo_env, desc, args):
    """GitClient._run doit lever PermissionError pour les commandes interdites."""
    _, git = repo_env
    with pytest.raises(PermissionError):
        git._run(*args)


@pytest.mark.require_all_passed
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