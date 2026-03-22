# E2E Tests

## Overview

End-to-end tests that run the real `zp` CLI as a subprocess against a real GitHub repo. Tests make actual modifications: commits, tags, GitHub releases, asset uploads, GPG signatures.

Tests are not isolated. They share an external repo and run in order (`test_00_*`, `test_01_*`, ...). Ordering is enforced by `pytest_collection_modifyitems` in `conftest.py`.

## Requirements

- A dedicated GitHub sandbox repo
- `gh` (GitHub CLI) authenticated
- `gpg` with at least one secret key (for signing tests)
- `git` with push access to the repo
- A `.zenodo.test.env` file in `tests/`

## Configuration

### `.zenodo.test.env`

```env
GIT_REPO_PATH="/path/to/sandbox-repo"
GIT_TEMPLATE_SHA="<commit sha>"
GPG_UID="<gpg key fingerprint or email>"
```

- `GIT_REPO_PATH`: local path to the test repo (must be cloned and have a `zenodo_config.yaml`)
- `GIT_TEMPLATE_SHA`: commit SHA used as template for repo reset (see below)
- `GPG_UID`: GPG key fingerprint or email used for signing tests. Available as `fix_gpg_uid` fixture. Tests also verify that signing works without an explicit UID (ZP falls back to the default GPG key).

## Repo reset

After each test, the repo is reset to its template state via `reset_test_repo()`:

1. Delete orphaned **draft releases** on GitHub (via REST API, since `gh release list` does not show drafts)
2. Delete orphaned **remote tags** (tags with no associated release)
3. `git reset --hard HEAD` + `git clean -fd` (clean dirty local state)
4. `git checkout -f main` (force switch to main branch)
5. Delete all **local branches** except main
6. `git reset --hard origin/main` + delete all **local tags**
7. `git rm -rf .` then `git checkout <GIT_TEMPLATE_SHA> -- .` (restore template content)
8. `git add . && git commit && git push` (push clean state)

The **template SHA** (`GIT_TEMPLATE_SHA`) is a reference commit in the repo. The reset restores the file content from that commit while preserving the branch history. It replaces the working tree only, not the branch pointer.

## Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `fix_log_dir` | session | `tests/logs/` for output logs |
| `fix_repo_dir` | session | Path to the test repo |
| `fix_repo_git` | session | `GitClient` instance for the test repo |
| `fix_branch_name` | session | Main branch name from repo config |
| `fix_gpg_uid` | session | GPG key UID from `.zenodo.test.env` |
| `repo_env` | function | Yields `(repo_dir, git)`, auto-resets after the test |

### Auto-reset

The `repo_env` fixture automatically resets the repo after each test. To disable:

```python
# Per test
@pytest.mark.no_auto_reset
def test_something(repo_env):
    ...

# Per file
pytestmark = pytest.mark.no_auto_reset
```

Some test files (e.g. `test_08_tag.py`) manage reset manually to control the order of operations (cleanup GitHub releases before git reset).

## Test files

| File | What it tests | Repo type |
|------|---------------|-----------|
| `test_00_reset` | Initial repo reset | Real |
| `test_01_run` | Basic `zp release` launch | Real |
| `test_02_config` | Config loading, validation, prompts, signing, hashing | tmp_path |
| `test_03_git` | Git checks (branch, sync, modifications, tags) | Real |
| `test_04_archive` | Archive formats (zip/tar/tar.gz), hashes, tree hash, contents | tmp_path |
| `test_05_release_archive` | Release pipeline: generated_files, project, pattern, compile | Real |
| `test_06_sign` | GPG signing, manifest, GitHub assets | Real |
| `test_07_env` | Environment variables passed to Makefile | Real |
| `test_08_tag` | Tags, releases, drafts, conflict scenarios | Real |

**tmp_path** = temporary repo created by pytest (bare remote + local clone), no GitHub interaction.
**Real** = GitHub sandbox repo, real commits/tags/releases/assets.

## Test mode and NDJSON output

When run with `--test-mode`, ZP writes all its events as NDJSON (newline-delimited JSON) to stdout instead of the normal console output. Each line is a structured event:

```json
{"type": "step_ok", "msg": "On {branch} branch", "name": "git.branch_check", "data": {"branch": "main"}}
{"type": "fatal", "msg": "...", "error_type": "GitError", "name": "git.not_on_main"}
{"type": "data", "code": "file_hashes", "value": {"file.zip": {"sha256": "abc..."}}}
```

Tests parse this stream using `tests/utils/ndjson.py` and check specific events:

```python
from tests.utils.ndjson import find_by_name, find_errors, has_step_ok, find_data

# Check that a step succeeded
assert has_step_ok(result.events, "git.branch_check")

# Check that a specific error was emitted
assert find_by_name(result.events, "git.not_on_main")

# Get structured data from events
file_hashes = find_data(result.events, "file_hashes")
```

### Test config and prompts

Tests control interactive prompt responses via a test config dict passed to `ZpRunner.run_test()`:

```python
_TEST_CONFIG = {
    "prompts": {
        "enter_tag": "v1.0.0",
        "release_title": "",
        "confirm_build": "yes",
        "confirm_publish": "no",
        "confirm_persist_overwrite": "yes",
    },
    "verify_prompts": False,
    "cli": {
        "args": ["--sign", "--no-compile"],
    },
}
```

- `prompts`: automatic responses to ZP prompts (tag name, title, confirmations, etc.)
- `verify_prompts`: when `True`, asserts that the prompts received match the expected ones exactly
- `cli.args`: extra CLI arguments appended to the `zp` command (e.g. `--sign`, `--no-compile`)

`ZpRunner.run_test()` writes temporary config files and passes them to ZP via `--config` and `--test-config`.

### Error validation

The `fail_on` parameter controls which event types cause the test to fail automatically:

```python
# Fail on fatal + error (default)
result = runner.run_test("release", config=config, fail_on=None)

# Ignore all errors (manual verification)
result = runner.run_test("release", config=config, fail_on="ignore")

# Also fail on warnings
result = runner.run_test("release", config=config, fail_on={"fatal", "error", "warn"})
```

## Running tests

```bash
# All tests (in order)
uv run pytest tests/e2e/ -v

# Single file
uv run pytest tests/e2e/test_08_tag.py -v

# Single function
uv run pytest tests/e2e/test_08_tag.py::test_create_release -v

# With print output visible
uv run pytest tests/e2e/test_08_tag.py -v -s
```

## What tests modify on GitHub

- Creates and deletes **releases** (published and draft)
- Creates and deletes **tags** (lightweight and annotated)
- Uploads and deletes release **assets** (files, signatures)
- The reset cleans up orphaned drafts and tags

The sandbox repo is meant for this. Do not point tests at a production repo.
