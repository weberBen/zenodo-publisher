# CLAUDE.md -- zenodo-publisher

## Project

Lightweight local CLI (`zp`) to publish LaTeX/compiled projects on Zenodo from a git repo.
Designed for a single maintainer with step-by-step console feedback.

- **Language**: Python 3.10+
- **Package manager**: `uv`
- **Build**: `hatchling`
- **Entry points**: `zp` / `zenodo-publisher` -> `release_tool.cli:main`
- **Tests**: E2E test suite via pytest (see `tests/README.md`)

## Structure

```
release_tool/
├── cli.py                          # Argparse auto-generated from ConfigOption + --sign/--no-sign
├── __main__.py                     # python -m release_tool
├── errors.py                       # ZPError base + normalize_name (prefix.name.suffix with dedup)
├── prompts.py                      # Interactive prompts (init_prompts, confirm levels)
├── config/
│   ├── schema.py                   # ConfigOption dataclass + dedup_args (merge default/user args)
│   ├── common.py                   # CommonConfig base (resolution: CLI > YAML > env > default)
│   ├── yaml.py                     # Load zenodo_config.yaml, traverse_yaml
│   ├── env.py                      # Load .zenodo.env (sensitive vars only)
│   ├── release.py                  # ReleaseConfig + RELEASE_OPTIONS + signing + generated_files
│   ├── archive.py                  # ArchiveConfig + ARCHIVE_OPTIONS
│   ├── signing.py                  # SigningConfig, SignMode + parse_signing_config()
│   ├── generated_files.py          # FileEntry, FileEntryKind, PublisherDestinations + parse
│   ├── pattern_overlap.py          # validate_no_pattern_overlap (FSM via interegular)
│   ├── test.py                     # Test mode config (NDJSON output, prompt responses)
│   ├── transform_common.py         # Shared transforms (tar, gzip, hash, COMMIT_FIELD_MAP)
│   └── transform_release.py        # Release transforms (compile_dir, make_args)
├── pipeline/
│   ├── _common.py                  # setup_pipeline()
│   ├── release.py                  # Release pipeline (14 steps)
│   └── archive.py                  # Standalone archive pipeline
├── output.py                       # Structured logging + test mode NDJSON
├── git_operations.py               # Git + GitHub CLI (gh) + draft release check
├── zenodo_operations.py            # Zenodo/InvenioRDM client (ArchivedFile-based)
├── archive_operation.py            # ArchivedFile dataclass + hashing + manifest
├── gpg_operations.py               # GPG signing via python-gnupg
├── latex_build.py                  # Compilation via make deploy (ZP_* env vars passed)
├── file_utils.py                   # File persistence (ArchivedFile-based)
└── subprocess_utils.py             # Subprocess wrapper with debug logging
```

## Commands

```bash
uv run zp release              # Full release pipeline (default when no subcommand)
uv run zp release --sign       # Release with GPG signing
uv run zp archive --tag v1.0.0 # Standalone archive
uv run zp --help               # CLI help
```

---

## Configuration system

### Two config files

| File | Content | Tracked? |
|------|---------|----------|
| `zenodo_config.yaml` | All options (project name, compile, signing, archive, generated_files, zenodo, github) | Yes |
| `.zenodo.env` | Sensitive vars only (`ZENODO_TOKEN`, optionally `ZENODO_CONCEPT_DOI`) | No |

### Resolution order (highest wins)

```
CLI flag > zenodo_config.yaml > os.environ > .zenodo.env > default
```

### ConfigOption dataclass (`config/schema.py`)

Every config option is declared as a `ConfigOption`:

```python
ConfigOption(
    name="compile_dir",           # attribute name on config object
    env_key=None,                 # env var name (e.g. "ZENODO_TOKEN")
    yaml_path="compile.dir",     # dot-separated path in YAML
    type="str",                   # "str", "bool", "list", "store_true"
    default="",                   # default value
    cli=True,                     # whether to generate CLI flag
    help="Compile directory",
    transform=_resolve_compile_dir,  # post-coercion transform(value, project_root)
    validate=None,                   # custom validation function
    choices=None,                    # allowed values list
    nullable=False,                  # whether None is acceptable
    parse=None,                      # custom coercion function
    extra_attrs=None,                # extra attributes from tuple transform returns
)
```

### Config loading flow (`CommonConfig.__init__()`)

For each `ConfigOption`:
1. `_resolve_value()` -- priority: CLI > yaml_path > os.environ > env_file > default
2. `validate_type()` -- raw type check
3. `_coerce()` -- handles native YAML types (bool, list already correct), string coercion for env/CLI:
   - bool: `"true"` -> True, `"false"` -> False
   - list: `"a,b,c"` -> `["a", "b", "c"]`
   - str: rejects "true"/"false" (looks like bool), rejects "," (looks like list)
4. `validate_choices()` -- checks value in allowed list
5. `opt.transform(value, project_root)` -- post-coercion (paths, args dedup), can return tuple with extra_attrs
6. `opt.validate()` -- custom business validation

### dedup_args (arg merging)

`dedup_args(default_args, user_args)` merges CLI-style arg lists. Last value wins:
- `--flag` -> simple presence
- `--key=value` -> override value
- `--no-X` removes `--X`
- `-Xvalue` -> override
- `KEY=value` -> override

Used for: `make_args`, `tar_extra_args`, `gzip_extra_args`, `gpg_extra_args`.

### Complex structures (parsed from YAML, not ConfigOption)

- `signing:` -> `SigningConfig` via `parse_signing_config()`
- `generated_files:` -> `list[FileEntry]` via `parse_generated_files()`

### Config subclasses

- `ReleaseConfig` = `COMMON_OPTIONS` + `RELEASE_OPTIONS` + signing + generated_files
- `ArchiveConfig` = `COMMON_OPTIONS` + `ARCHIVE_OPTIONS`

### CLI auto-generation (`cli.py`)

`_add_options()` iterates `_options` list to create argparse flags:
- Flag format: `--{name}` or `--{alias}` (from `_cli_aliases`)
- BooleanOptionalAction for bools (`--sign`/`--no-sign`)
- `store_true` for flags without value
- Hidden flags: `--test-mode`, `--test-config` use `argparse.SUPPRESS`
- Reserved flags: `--work-dir`, `--config`

### COMMON_OPTIONS

| Name | yaml_path | Type | Default | Notes |
|------|-----------|------|---------|-------|
| `project_name_prefix` | `project_name.prefix` | str | "" | Empty = uses root dir name |
| `project_name_suffix` | `project_name.suffix` | str | `-{tag_name}` | Validates: no `.`, only `{tag_name}` or `{sha_commit}` |
| `debug` | `debug` | bool | False | |
| `archive_format` | `archive.format` | str | `zip` | Choices: zip, tar, tar.gz |
| `archive_tar_extra_args` | `archive.tar_extra_args` | list | (deduped with TAR_DEFAULT_ARGS) | |
| `archive_gzip_extra_args` | `archive.gzip_extra_args` | list | (deduped with GZIP_DEFAULT_ARGS) | |
| `hash_algorithms` | `hash_algorithms` | list | [] | hashlib algos + "tree"/"tree256" |

### RELEASE_OPTIONS

| Name | yaml_path | Type | Default | Notes |
|------|-----------|------|---------|-------|
| `main_branch` | `main_branch` | str | `main` | |
| `compile_enabled` | `compile.enabled` | bool | True | |
| `compile_dir` | `compile.dir` | str | "" | Resolved: project_root / value. Empty = project_root |
| `make_args` | `compile.make_args` | list | "" | |
| `zenodo_token` | (ZENODO_TOKEN) | str | "" | env only, cli=False |
| `zenodo_concept_doi` | `zenodo.concept_doi` | str | "" | |
| `zenodo_api_url` | `zenodo.api_url` | str | `https://zenodo.org/api` | |
| `publication_date` | `zenodo.publication_date` | str | None | nullable, YYYY-MM-DD |
| `zenodo_force_update` | `zenodo.force_update` | bool | False | |
| `archive_dir` | `archive.dir` | str | None | nullable, resolved as Path |
| `gpg_sign` | `signing.sign` | bool | False | CLI alias: `--sign`/`--no-sign` |
| `check_gh_draft` | `github.check_draft` | bool | False | cli=False (slow, paginates all releases) |
| `prompt_validation_level` | `prompt_validation_level` | str | `light` | Choices: danger, light, normal, secure |

### ARCHIVE_OPTIONS

| Name | yaml_path | Type | Default | Notes |
|------|-----------|------|---------|-------|
| `tag` | `tag` | str | "" | Required |
| `output_dir` | `output_dir` | str | None | nullable, resolved as Path |
| `remote` | `remote` | str | None | nullable, git remote URL |
| `no_cache` | `no_cache` | store_true | False | Fetch from remote instead of local |

### Sensitive env keys

`SENSITIVE_ENV_KEYS = {"ZENODO_TOKEN", "ZENODO_CONCEPT_DOI"}` -- only these are allowed in `.zenodo.env`.

### YAML traversal

`traverse_yaml(config, "compile.dir")` follows dot-separated path. Returns None if any segment missing.

---

## Release pipeline (14 steps)

Sequential steps in `pipeline/release.py:_run_release()`:

### Step 1: Git check (`_step_git_check`)

Calls `check_up_to_date()` which runs checks **in this specific order** (first match raises):

1. `git fetch` (always)
2. `local_modifications` -- `git status --porcelain` non-empty -> `GitError("git.local_modifications")`
3. `unpushed_commits` -- `git log origin/main..HEAD --oneline` non-empty -> `GitError("git.unpushed_commits")`
4. `not_up_to_date` -- `git rev-parse main != git rev-parse origin/main` -> `GitError("git.not_up_to_date")`
5. `unpushed_tags` -- set difference between local and remote tags -> `GitError("git.unpushed_tags")`

**Order matters**: `unpushed_commits` before `not_up_to_date` because both cause ref divergence, but unpushed_commits is more specific and actionable.

### Step 2: Release (`_step_release`)

1. `get_latest_release()` -- `gh release list --exclude-drafts --limit 1 --json tagName,name`
2. If latest commit already released: skip
3. If `check_draft` enabled: `_check_no_draft_release()` scans all releases via REST API
4. `check_tag_validity()`:
   - Tag doesn't exist: OK
   - Tag exists, points to latest remote commit: OK (reuse)
   - Tag exists, wrong commit: `GitError("git.tag_invalid")`
5. Prompts: `enter_tag`, `release_title`, `release_notes`
6. `gh release create <tag> --title <title> --notes <notes>`

### Step 3: Commit info (`_step_commit_info`)

`git log -1 --format=%H%n%ct%n%cn%n%ce%n%an%n%ae%n%s` -- parses 7 fields (subject can contain newlines, handled with `maxsplit=6`).

Returns dict with ZP_* keys:
```
ZP_COMMIT_DATE_EPOCH, ZP_COMMIT_SHA, ZP_COMMIT_SUBJECT,
ZP_COMMIT_COMMITTER_NAME, ZP_COMMIT_COMMITTER_EMAIL,
ZP_COMMIT_AUTHOR_NAME, ZP_COMMIT_AUTHOR_EMAIL,
ZP_BRANCH, ZP_ORIGIN_URL
```
If tag available: `ZP_COMMIT_TAG`, `ZP_TAG_SHA` (tag object SHA via `git rev-parse <tag>`, differs from commit SHA for annotated tags. commit SHA resolved via `git rev-parse <tag>^{commit}`).

### Step 4: Project name (`_step_project_name`)

Resolves template: `{prefix}{suffix}` where suffix uses `{tag_name}` and/or `{sha_commit}`.
Sets `config.project_name` and `config.project_name_template = [prefix, "", suffix]`.

### Step 5: Compile (`_step_compile`)

Runs `make deploy` in `compile.dir` with env vars merged:
```python
cmd = ["make", "deploy"] + (make_args or [])
env = {**os.environ, **commit_env_vars}
```
Checks Makefile exists first. Raises `CompileError` on failure.

### Step 6: Re-check

Repeats git check + `verify_release_on_latest_commit()`.

### Step 7: Resolve generated files (`_step_resolve_generated_files`)

For each PATTERN entry:
1. Resolve `{project_name}` template (available after step 4)
2. `pattern.lstrip("/")` (leading / = project root, not filesystem)
3. `base = config.project_root or Path.cwd()`
4. `matches = sorted(base.glob(pattern))`
5. No matches -> `PipelineError("pipeline.no_match.{key}")`

### Step 8: Archive (`_step_archive`)

- **PATTERN**: `shutil.copy2(src, output_dir / src.name)` -- flat copy, filename only (no subdir)
- **PROJECT**: `git archive --format=zip --prefix={project_name}/ -o <output> <ref>`
  - Then `process_project_archive()`: extract zip, compute tree hashes, convert format if tar/tar.gz
  - Reproducible TAR env: `LC_ALL=C`, `TZ=UTC`, `SOURCE_DATE_EPOCH=0`

Creates `ArchivedFile` for each file.

### Step 9: Manifest (`_step_manifest`)

**Generated BEFORE hashes (step 10)**. Manifest entries contain only `key` (filename), not hashes.

Uses JCS (RFC 8785) for canonical JSON: deterministic serialization, same bytes = same hash.

Structure:
```json
{
  "version": {"label": "v1.0.0", "sha": "tag_object_sha"},
  "commit": {"sha": "abc...", "date_epoch": 1234567890, ...},
  "files": [{"key": "paper.pdf"}, {"key": "project.zip"}],
  "metadata": {"title": "...", "creators": [...]}
}
```

`manifest.files` lists entry keys to include (append `_sig` for signatures).
The manifest file itself is hashed in step 10.

### Step 10: Compute hashes (`_step_compute_hashes`)

Computes hashes for all ArchivedFile entries. Reads files in 8192-byte chunks.

Tree hashes: pre-computed in step 8 for PROJECT entries (single extraction). For non-archive files (PDF), falls back to hashlib equivalent: `tree` -> `sha1`, `tree256` -> `sha256`.

Hash dict per file: `{algo: {"type": algo, "value": hex, "formatted_value": "algo:hex"}}`

### Step 11: Sign (`_step_sign`)

Two modes:
- **FILE** (`sign_mode: file`): `gpg.sign_file(file, detach=True)` -> `file.pdf.asc`
- **FILE_HASH** (`sign_mode: file_hash`): write `algo:hexvalue` to temp file, sign that -> `file.pdf.sha256.asc`

Uses `python-gnupg` library (wraps gpg binary internally).

GPG key resolution: explicit `gpg.uid` > `default-key` from `~/.gnupg/gpg.conf` > first secret key.

After signing: verifies signature with `gpg.verify_file()`, checks fingerprint match.

Signature files are appended to `archived_files` list as `ArchivedFile(kind="signature", is_signature=True)`.

### Step 12: Compute identifiers (`_step_compute_identifiers`)

Per-file alternate identifiers pushed to Zenodo `metadata.identifiers`. Format: `{prefix}{algo}:{hex}` (e.g. `sha256:abc123...`). Not related to manifest.

### Step 13: Publish (`_step_publish`)

Routes each file to destinations per `publishers` config:
- **Zenodo**: checks `is_up_to_date()` (compares version + MD5 hashes), uploads via InvenioRDM API
- **GitHub**: `gh release upload <tag> <file>`, compares sha256 to detect changes, prompts for `--clobber`

### Step 14: Persist (`persist_files`)

Copies files to `archive_dir/{tag}/` via `shutil.move()`. Updates `entry.file_path` in-place.
Prompts for overwrite if files exist (with "apply all" option: `yall`/`nall`).

---

## Generated files system

### Three types (`FileEntryKind`)

| Type | Key | What it does |
|------|-----|--------------|
| `PATTERN` | custom | File matched by glob pattern in project |
| `PROJECT` | `project` (reserved) | Git archive ZIP of the repository |
| `MANIFEST` | `manifest` (reserved) | JSON manifest in JCS format |

### FileEntry dataclass

```python
@dataclass
class FileEntry:
    key: str                          # config key (e.g. "paper", "project")
    kind: FileEntryKind
    pattern: str | None               # resolved pattern (after template substitution)
    pattern_template: str | None      # original pattern with {vars}
    rename: bool                      # rename using project_name template
    sign: bool | None                 # per-file override (None = use global)
    sign_mode: SignMode | None        # per-file override
    archive: bool                     # persist to archive_dir
    publishers: PublisherDestinations
    identifier: IdentifierConfig | None
    manifest_config: ManifestInclusion | None
    resolved_paths: list[Path]        # populated at runtime by step 7
```

Methods:
- `effective_sign(global_sign)` -- returns per-file `sign` or global
- `effective_sign_mode(global_mode)` -- returns per-file `sign_mode` or global

### ArchivedFile dataclass

```python
@dataclass
class ArchivedFile:
    file_path: Path
    config_key: str               # references FileEntry.key
    filename: str
    extension: str
    kind: str                     # "generated", "project", "manifest", "signature"
    is_preview: bool = False      # True if PDF
    is_signature: bool = False
    has_signature: bool = False
    persist: bool = True
    hashes: dict = {}             # {algo: {"type", "value", "formatted_value"}}
    publishers: PublisherDestinations | None = None
    signed_file_key: str | None = None
    identifier_value: str | None = None
```

### Pattern resolution details

Patterns resolve in two phases:

**Phase 1: Config time** (`_resolve_pattern_templates()` in `ReleaseConfig.__init__`):
- `{compile_dir}` -> relative path of compile.dir (relative to project_root)
- `{project_root}` -> absolute path to project root
- `{project_name}` -> left as-is (not yet available)

**Phase 2: Runtime** (`_step_resolve_generated_files()` in pipeline):
- `{project_name}` -> resolved from tag
- Leading `/` stripped
- `base.glob(pattern)` from project_root

### Flat copy and collision detection

Step 8 copies matched files as `output_dir / src_path.name` (filename only, no subdirectory). If two files produce the same destination name, the pipeline fails with `PipelineError("pipeline.archive.collision.{key}")` instead of silently overwriting.

### Rename behavior

`rename: true` on a PATTERN entry renames the file to `{project_name}{ext}`. When multiple files share the same extension, the original stem is appended: `{project_name}_{original_stem}{ext}`. Files with a unique extension keep the clean name.

`rename: true` on a PROJECT entry uses `project_name` as archive name/prefix (e.g. `MyProject-v1.0.0.zip`). `rename: false` (default) uses the repo directory name (e.g. `my-repo.zip`).

### Pattern overlap detection (`pattern_overlap.py`)

Uses `interegular` FSM library. Checks segment-by-segment. Normalizes paths (resolve `..`, remove `.`). Different depth: checks if shorter is prefix of longer. Raises `ConfigError("config.generated_files.pattern_overlap")`.

### Identifier constraints

- Source can be `"file"` or `"sig_file"` (sig_file requires `sign: true`)
- Glob patterns that match multiple files cannot use identifiers
- User keys must not end with `_sig` (reserved for signature references)

---

## Git operations (`git_operations.py`)

### Exact subprocess commands

| Operation | Command |
|-----------|---------|
| Current branch | `git rev-parse --abbrev-ref HEAD` |
| Fetch remote | `git fetch` |
| Local modifications | `git status --porcelain` |
| Unpushed commits | `git log origin/{branch}..HEAD --oneline` |
| Local ref | `git rev-parse {branch}` |
| Remote ref | `git rev-parse origin/{branch}` |
| Commit info | `git log -1 --format=%H%n%ct%n%cn%n%ce%n%an%n%ae%n%s {commit}` |
| Tag commit | `git rev-parse {tag}^{commit}` (dereferences annotated tags) |
| Tag object SHA | `git rev-parse {tag}` |
| Fetch tag | `git fetch origin tag {tag}` |
| Local tags | `git tag -l` |
| Remote tags | `git ls-remote --tags --refs origin` |
| Remote URL | `git remote get-url origin` |
| Create archive | `git archive --format=zip --prefix={project_name}/ -o {output} {ref}` |
| Tree hash init | `git init [--object-format={sha256}] .` |
| Tree hash | `git add --all && git write-tree` |
| Tar pack | `tar {TAR_DEFAULT_ARGS} -cf {output} -C {parent} {dirname}` |
| Gzip | `gzip {GZIP_DEFAULT_ARGS} {tar_path}` |

### GitHub CLI (gh) commands

| Operation | Command |
|-----------|---------|
| Latest release | `gh release list --exclude-drafts --limit 1 --json tagName,name` |
| Release details | `gh release view {tag} --json tagName,name,body,isDraft` |
| Check draft | `gh api repos/{owner}/{repo}/releases --paginate --jq '.[] \| select(.draft == true and .tag_name == "{tag}") \| .id'` |
| Create release | `gh release create {tag} --title {title} --notes {notes}` |
| Asset digest | `gh api repos/{owner}/{repo}/releases/tags/{tag} --jq '.assets[] \| select(.name == "{name}") \| .digest'` |
| Upload asset | `gh release upload {tag} {file} [--clobber]` |

### Tag subtleties

- **Lightweight tag**: pointer to commit. SHA = commit SHA.
- **Annotated tag**: separate git object with own SHA, metadata. `git archive` dereferences to commit.
- `git rev-parse {tag}^{commit}` works for both types (dereferences annotated to commit).
- `gh release create` creates tag on remote only; need `git fetch --tags` to get it locally.

### Draft release behavior

- GitHub drafts are invisible to `gh release list` and `/releases/tags/{tag}` API
- `gh release create` with tag matching draft silently converts draft to published
- Detection requires REST API `/repos/{owner}/{repo}/releases` with pagination + JQ filter
- `github.check_draft: true` enables this (opt-in, slow)

### Remote tag refs

`git ls-remote --tags --refs` output includes `^{}` lines for dereferenced tags. These are filtered out in `has_unpushed_tags()`.

---

## GPG signing (`gpg_operations.py`)

Uses `python-gnupg` library (internally calls `gpg` binary).

### Key resolution order

1. Explicit `gpg.uid` from config
2. `default-key` from `~/.gnupg/gpg.conf`
3. First secret key in keyring

### Signing flow

```python
gpg = gnupg.GPG()
sig = gpg.sign_file(file_handle, keyid=uid, detach=True, output=sig_path, extra_args=extra_args)
# Then verify:
gpg.verify_file(sig_handle, data_filename=original_file)
```

### Signature format

- `.asc` if `--armor` in extra_args (default)
- `.sig` if `--no-armor` in extra_args
- Naming: `file.pdf.asc` (FILE mode) or `file.pdf.sha256.asc` (FILE_HASH mode)

### Two hash concepts

- **`sign_hash_algo`** (ZP config, default `sha256`): which hash computes the digest written to the temp file in FILE_HASH mode. Changes the signed content.
- **GPG digest algo** (`gpg.extra_args: ["--digest-algo", "SHA512"]`): GPG's internal signature hash. Independent from sign_hash_algo.

### Manifest signature

The manifest is NOT signed directly. Its identifier hash (`algo:hex`) is written to a text file, that text file is signed. Verification must match byte-for-byte (no trailing newline).

---

## Archive system (`archive_operation.py`)

### ArchiveResult dataclass

```python
@dataclass
class ArchiveResult:
    file_path: Path
    archive_name: str
    format: str  # "zip", "tar", "tar.gz"
```

### Archive creation flow

1. `git archive --format=zip --prefix={project_name}/ -o {output} {ref}`
2. If tree hashes needed or format != zip: extract zip to temp dir
3. Compute tree hashes: `git init` in extracted dir, `git add --all`, `git write-tree`
4. If tar/tar.gz: repack with deterministic args, delete original zip
5. Return final path + format

### Reproducible TAR defaults

```python
TAR_DEFAULT_ARGS = [
    "--sort=name", "--format=posix",
    "--pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime",
    "--mtime=1970-01-01 00:00:00Z",
    "--numeric-owner", "--owner=0", "--group=0",
    "--mode=go+u,go-w",
]
GZIP_DEFAULT_ARGS = ["--no-name", "--best"]
```

Environment for tar: `LC_ALL=C`, `TZ=UTC`, `SOURCE_DATE_EPOCH=0`.

### Tree hash

Git tree hash = hash of file tree (content + permissions + names), excluding commits/tags/metadata.

- `tree` -> SHA-1 (git default object format)
- `tree256` -> SHA-256 (`git init --object-format=sha256`)

Computed by: init temp git repo, config user, `git add --all`, `git write-tree`. Finally removes `.git`.

For non-archive files (e.g. PDF), falls back to hashlib: `tree` -> `sha1`, `tree256` -> `sha256`.

### Project name and checksums

Project name is embedded as archive prefix (`ProjectName-tag/`). Changing it changes md5/sha256 but NOT tree hash (tree hash depends on content only).

### Manifest generation

`generate_manifest()` creates dict, `manifest_to_file()` serializes via `jcs.canonicalize()` (RFC 8785). Manifest filename: `manifest{suffix}.json` where suffix comes from `project_name_template[-1]`.

Excludes signature entries (`is_signature=True`).

---

## Zenodo operations (`zenodo_operations.py`)

### ZenodoPublisher

Uses `inveniordm-py` library (`InvenioAPI`).

### Key methods

- `is_up_to_date(tag_name, archived_files)` -> `(bool, msg, record_info)`:
  - Compares version (tag) and MD5 hashes
  - Excludes signatures if gpg_sign enabled (timestamps differ between runs)
- `publish_new_version(archived_files, tag_name, identifiers)`:
  1. Get last record version
  2. Check/discard existing draft
  3. Create new version draft
  4. Upload files (sets `default_preview` for PDF)
  5. Load `.zenodo.json` overrides (validates no `version` field, no identifier collisions)
  6. Update metadata: version, publication_date, identifiers
  7. Publish
  8. Return `{"doi", "record_url"}`

### Metadata handling

- `.zenodo.json` uses InvenioRDM format (NOT legacy Zenodo)
- `version` forbidden in .zenodo.json (pipeline sets it)
- `publication_date` allowed (overrides config, with warning)
- `identifiers` allowed but must not collide with pipeline hash identifiers (prefix match)

### Draft handling

ZP **discards existing drafts** on the deposit before creating a new one. If someone edits via web UI, changes are lost.

---

## Compilation (`latex_build.py`)

```python
cmd = ["make", "deploy"] + (make_args or [])
env = {**os.environ, **env_vars}  # ZP_* vars merged
subprocess.run(cmd, cwd=compile_dir, env=env)
```

Checks Makefile exists first. Raises `CompileError` on failure.

`compile.dir` defaults to project root when empty (`Path("root") / "" == Path("root")`). ZP validates compile_dir exists even if `compile.enabled: false`.

---

## Error system (`errors.py`)

### ZPError base

`normalize_name(name, prefix, suffix)` assembles non-empty parts with dots, deduplicates consecutive segments:
```
normalize_name("foo", prefix="git", suffix=None) -> "git.foo"
normalize_name("git.bar", prefix="git") -> "git.bar" (not "git.git.bar")
normalize_name(None, prefix="git", suffix="check") -> "git.check"
```

### Subclasses and prefixes

| Class | _prefix | Example error name |
|-------|---------|-------------------|
| `ZPError` | None | (base, not used directly) |
| `GitError` | `git` | `git.local_modifications`, `git.not_on_main`, `git.tag_invalid` |
| `GitHubError` | `github` | `github.release_not_found` |
| `ConfigError` | `config` | `config.generated_files.pattern_overlap`, `config.release.compile_dir.not_found` |
| `GpgError` | `gpg` | `gpg.signing_failed` |
| `CompileError` | `compile` | `compile.make_failed` |
| `PipelineError` | `pipeline` | `pipeline.no_match.paper` |

Errors carry: `message`, `name` (scoped), `exc` (original exception).

---

## Output system (`output.py`)

### Event hierarchy

```
step / step_ok / step_warn     -> pipeline phase headers
info / info_ok                 -> top-level messages
detail / detail_ok             -> indented sub-operations
warn / error / fatal / debug   -> severity levels
data                           -> structured data (NDJSON in test mode)
cmd                            -> subprocess command (debug only)
prompt / confirm               -> interactive prompts
```

### Test mode (`--test-mode`)

All events written as NDJSON to stdout. Each line:
```json
{"type": "step_ok", "msg": "On {branch} branch", "name": "git.branch_check", "data": {"branch": "main"}}
{"type": "fatal", "msg": "...", "error_type": "GitError", "name": "git.not_on_main"}
{"type": "data", "code": "file_hashes", "value": {"file.zip": {"sha256": "abc..."}}}
```

Prompts answered from `--test-config` file automatically.

### Subprocess logging

`subprocess_utils.run()` wraps `subprocess.run()`: logs command as `output.cmd()`, logs result as `output.data("subprocess_result", {...})` with cmd, returncode, stdout, stderr.

---

## Prompt system (`prompts.py` + `output.py`)

### Prompt types

- **Text**: `enter_tag`, `release_title`, `release_notes` -- free text input
- **Confirm**: `confirm_build`, `confirm_publish`, `confirm_github_overwrite`, `confirm_persist_overwrite`, `confirm_gpg_key`

### Validation levels

| Level | Behavior |
|-------|----------|
| `danger` | Enter key confirms (no typing required) |
| `light` | Type `y` or `yes` |
| `normal` / `complete` | Type full option (yes/no) |
| `secure` | Type exact project root directory name |

### PromptResult

```python
@dataclass
class PromptResult:
    name: str          # prompt name
    is_accept: bool    # affirmative
    value: str         # option name (confirm) or text (text mode)
```

### Test mode prompts

In test mode, `Prompt.ask()` looks up response from `test_config.prompts[name]`. No user interaction.

---

## File persistence (`file_utils.py`)

`persist_files(entries, archive_dir, tag_name)`:
1. Filter entries by `entry.persist`
2. Create `archive_dir/{tag_name}/`
3. For each file: check if dest exists, prompt for overwrite
4. `shutil.move()` to destination
5. Update `entry.file_path` in-place

Overwrite prompt has "apply all" logic: `yall` (overwrite all remaining), `nall` (skip all remaining).

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `inveniordm-py` | Zenodo/InvenioRDM API client |
| `jcs` | JSON Canonicalization (RFC 8785) |
| `python-gnupg` | GPG signing (wraps gpg binary) |
| `pyyaml` | YAML parsing |
| `requests` | HTTP |
| `interegular` | Pattern overlap detection (FSM intersection) |

External tools: `git`, `gh` (GitHub CLI), `make`, `gpg`, `tar`, `gzip`

---

## Tests

E2E test suite in `tests/e2e/`. Tests run the real `zp` CLI as subprocess against a GitHub sandbox repo. See `tests/README.md` for full setup.

```bash
uv run pytest tests/e2e/ -v           # All tests (ordered by filename)
uv run pytest tests/e2e/test_08_tag.py -v  # Single file
uv run pytest tests/e2e/test_08_tag.py::test_create_release -v  # Single function
uv run pytest tests/e2e/test_08_tag.py -v -s  # With print output
```

### Test files

| File | Tests | Repo |
|------|-------|------|
| `test_00_reset` | Initial repo reset | Real |
| `test_01_run` | Basic `zp release` launch | Real |
| `test_02_config` | Config loading, validation, prompts | tmp_path |
| `test_03_git` | Git checks (branch, sync, mods, tags) | Real |
| `test_04_archive` | Archive formats, hashes, tree hash, contents | tmp_path |
| `test_05_release_archive` | Release pipeline archive step | Real |
| `test_06_sign` | GPG signing, manifest, GitHub assets | Real |
| `test_07_env` | Makefile env vars, persist overwrite | Real |
| `test_08_tag` | Tags, releases, drafts, conflicts | Real |
| `test_09_override` | Per-file sign_mode, hash_algo, GPG digest, rename | Real |
| `test_10_pattern` | Pattern resolution, overlap, wildcards, compile_dir | Mixed |

### Test ordering

Tests sorted by filename number (`test_00_*`, `test_01_*`, ...) via `pytest_collection_modifyitems`. Tests within a file run in function definition order.

### ZpRunner (`tests/utils/cli.py`)

Subprocess wrapper. All tests call ZP via: `uv run --project <ZP_ROOT> zp <args>`.

**Key method: `run_test()`**:
```python
runner.run_test("release",
    config={...},           # -> written to tmp zenodo_config.yaml, passed via --config
    test_config={           # -> written to tmp test.config.yaml, passed via --test-config
        "prompts": {"enter_tag": "v1.0.0", "confirm_build": "yes", ...},
        "verify_prompts": False,
        "cli": {"args": ["--sign", "--no-compile"]},
    },
    log_path=fix_log_path,
    fail_on=None,           # None={"fatal","error"}, "ignore", or set
    env={"ZENODO_TOKEN": "fake"},  # extra env vars
)
```

Returns `ZpResult(returncode, stdout, stderr, events, trace, http_requests)`.

### NDJSON parsing (`tests/utils/ndjson.py`)

```python
parse_stream(stdout) -> list[dict]              # parse NDJSON lines
find_errors(events) -> list[dict]               # filter error + fatal
find_warnings(events) -> list[dict]             # filter warn
has_step_ok(events, "git.branch_check") -> bool # check step completed
find_by_name(events, "git.not_on_main") -> dict # find first event by name
find_all_by_name(events, name) -> list[dict]    # find all events by name
find_data(events, "file_hashes") -> any         # get data event value
verify_prompts(events, expected_prompts)         # assert prompt names match
```

Name matching supports prefix: `"config_error"` matches `"config_error.git.no_root"`.

### GitClient (`tests/utils/git.py`)

Test-side git wrapper. Key methods:
```python
git = GitClient(repo_dir)
git.add_file("path", "content")
git.add_and_commit("msg")
git.push("origin", "main")
git.tag_create("v1.0.0", annotated=True, msg="release")
git.tag_delete("v1.0.0", remote=True)
git.branch_checkout("feature", create=True)
git.is_clean() -> bool
git.rev_parse("HEAD") -> str
git.reset_repo(branch, template_sha)  # full reset for test cleanup
```

### GithubClient (`tests/utils/github.py`)

Test-side GitHub wrapper via `gh` CLI:
```python
gh = GithubClient(repo_dir)
gh.has_release("v1.0.0") -> bool
gh.create_release("v1.0.0", title="Release")
gh.delete_release("v1.0.0", cleanup_tag=True)
gh.list_releases() -> list[dict]
gh.list_draft_releases() -> list[dict]           # REST API (gh release list misses drafts)
gh.list_release_assets("v1.0.0") -> list[dict]
gh.upload_asset("v1.0.0", file_path)
gh.delete_tag("v1.0.0", dangerous_delete=True)
gh.get_tag_info("v1.0.0") -> dict                # resolves annotated vs lightweight
```

### Repo reset mechanism (`conftest.py`)

Two-pass reset between tests (via `repo_env` fixture teardown):

```python
# Pass 1: restore template, commit leftovers
git.reset_repo(branch, template_sha)   # reset --hard, clean -fd, rm -rf, checkout template
git.add_and_commit()                    # git add . picks up leftover ignored files
git.push()

# Pass 2: now leftovers are tracked, git rm removes them
git.reset_repo(branch, template_sha)
git.add_and_commit()
git.push()
```

**Why two passes**: `git clean -fd` does not remove ignored files. If a test created files and added their directory to `.gitignore`, those files survive pass 1's `git clean`. After template `.gitignore` is restored, they become untracked and get committed by `git add .`. Pass 2's `git rm -rf .` removes them since they're now tracked.

Before git reset: cleans up orphaned GitHub state (draft releases via REST API, orphan remote tags).

### Sandbox (`tests/utils/sandbox.py`)

Runs ZP inside bubblewrap (`bwrap`):
- Read-only root filesystem
- Explicit writable paths (`rw_paths`)
- Optional network isolation (`--unshare-net`)
- Optional strace tracing (tracks `openat`, `execve`, `connect`)

```python
sandbox = SandboxConfig(rw_paths=[repo_dir], allow_network=True, trace=True)
runner = ZpRunner(repo_dir, sandbox=sandbox)
result = runner.run(...)
result.trace.files       # files accessed
result.trace.commands    # commands executed
result.trace.connections # network connections
```

### HTTP proxy (`tests/utils/proxy.py`)

Captures all HTTP requests via mitmproxy (`mitmdump`):
```python
runner = ZpRunner(repo_dir, use_proxy=True, proxy_port=8888)
result = runner.run(...)
for req in result.http_requests:
    print(req["method"], req["url"], req["status"])
```

### Fixtures summary

| Fixture | Scope | Yields |
|---------|-------|--------|
| `fix_log_dir` | session | `Path` tests/logs/ |
| `fix_log_path` | function | Auto-named log path from test node ID |
| `fix_repo_dir` | session | Test repo path |
| `fix_repo_git` | session | `GitClient` |
| `fix_branch_name` | session | Main branch name |
| `fix_gpg_uid` | session | GPG UID from `.zenodo.test.env` |
| `repo_env` | function | `(repo_dir, git)`, auto-resets after |
| `pattern_env` | function | `(repo_dir, git, gh, archive_dir, gpg_uid)` |

Opt out of auto-reset: `@pytest.mark.no_auto_reset` per test, or `pytestmark = pytest.mark.no_auto_reset` per file.

---

## Conventions

### Commits

Format: `type(scope): message` in English, lowercase.
Types: `fix`, `feat`, `refactor`, `test`.

### Code

- No docstrings on internal functions unless complex
- `# ---` comments to separate sections
- Python 3.10+ type hints (`X | None` not `Optional[X]`)
- Specialized exceptions per module

### Branches

- `main` = main branch
- Feature branches: `feat-*`
