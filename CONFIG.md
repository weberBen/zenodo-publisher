# Configuration Reference

This document provides a detailed walkthrough of a complete `.zp.yaml` configuration. Each section explains what the options do, how they interact, and what output they produce at each pipeline step.

See also: [`examples/zp.yaml.example`](./examples/zp.yaml.example) for a minimal template.

## Full example

The following configuration is used as the running example throughout this document. It covers: LaTeX compilation, GPG signing, two timestamping modules (DigiCert + OpenTimestamps), manifest generation, and multi-platform publishing.

```yaml
project_name:
  prefix: "MyProject"
  suffix: "-{tag_name}"

modules:
  digicert_timestamp:
    full_chain: true
  ots_timestamp:
    calendars:
      - https://alice.btc.calendar.opentimestamps.org
      - https://bob.btc.calendar.opentimestamps.org
      - https://finney.calendar.eternitywall.com
    nonce: true
    upgrade:
      save_header: true

main_branch: main
github:
  check_draft: false

compile:
  enabled: true
  dir: papers/latex

archive:
  format: zip
  dir: papers/latex/releases
  types: [file, sig]

hash_algorithms: [md5, sha256, tree]
identity_hash_algo: sha256
identity_key: hash

signing:
  sign: true
  sign_mode: file

zenodo:
  api_url: "https://sandbox.zenodo.org/api"
  concept_doi: "153267"

generated_files:
  paper:
    pattern: "{compile_dir}/main.pdf"
    rename: true
    publishers:
      destination:
        file: [github, zenodo]
        sig: [github]
    publish_identity_hash:
      destination:
        file: [github, zenodo]
        sig: [github]

  project:
    archive_types: []
    publishers:
      destination:
        file: [zenodo]

  manifest:
    archive_types: [file, digicert_timestamp, ots_timestamp]
    commit_info: [sha, date_epoch]
    sign: true
    content:
      paper: [file]
      project: [file]
    publish_identity_hash:
      destination:
        file: [github, zenodo]
    publishers:
      destination:
        file: [github, zenodo]
        digicert_timestamp: [github]
        ots_timestamp: [github]
    modules:
      digicert_timestamp: {}
      ots_timestamp:
        input_types: [file, digicert_timestamp]

prompt_validation_level: danger
```

---

## Section-by-section breakdown

### `project_name`

```yaml
project_name:
  prefix: "MyProject"
  suffix: "-{tag_name}"
```

Controls how files are named when `rename: true` is set on a `generated_files` entry.

| Field | Value | Effect |
|-------|-------|--------|
| `prefix` | `"MyProject"` | Base name for renamed files. If empty, defaults to the project root directory name. |
| `suffix` | `"-{tag_name}"` | Appended after the prefix. `{tag_name}` is replaced by the git tag at runtime. `{sha_commit}` is also available. |

**Result with tag `v1.0.0`**: the resolved project name is `MyProject-v1.0.0`. Any file with `rename: true` will use this as its base name (e.g. `MyProject-v1.0.0.pdf`, `MyProject-v1.0.0.zip`).

**Constraints**: the suffix must not contain `.` (to avoid breaking extensions) and only `{tag_name}` or `{sha_commit}` are allowed as template variables.

---

### `modules`

```yaml
modules:
  digicert_timestamp:
    full_chain: true
  ots_timestamp:
    calendars:
      - https://alice.btc.calendar.opentimestamps.org
      - https://bob.btc.calendar.opentimestamps.org
      - https://finney.calendar.eternitywall.com
    nonce: true
    upgrade:
      save_header: true
```

Declares **global module configurations**. These are default settings passed to every file that uses the module. Per-file overrides can be set under `generated_files.<key>.modules.<name>`.

Modules run as **isolated subprocesses** (each in its own uv virtual environment). They execute at pipeline step 12, after signing and before publishing.

**Module execution order** follows the declaration order here: `digicert_timestamp` runs first, then `ots_timestamp`. This matters because `ots_timestamp` can reference output from `digicert_timestamp` via `input_types`.

#### `digicert_timestamp`

| Option | Value | Effect |
|--------|-------|--------|
| `full_chain` | `true` | Embeds the full DigiCert CA certificate chain inside each `.tsr` file, making it self-contained for verification without downloading external certificates. |

Requests a free [RFC 3161](https://www.rfc-editor.org/rfc/rfc3161) timestamp from DigiCert's TSA. The timestamp proves that a file with a given hash existed at a specific point in time. Produces a `.tsr` file per input file.

The module uses `identity_hash_algo` (here `sha256`) to hash the file before submitting the timestamp request.

#### `ots_timestamp`

| Option | Value | Effect |
|--------|-------|--------|
| `calendars` | 3 servers | OpenTimestamps calendar servers to submit the hash to. Defaults to the OTS pool (4 servers) if omitted. |
| `nonce` | `true` | Adds a privacy nonce so calendar servers never see the real file hash. A random nonce is prepended and SHA256-hashed before submission. |
| `upgrade.save_header` | `true` | After a successful proof upgrade, saves the Bitcoin block header as a `.blockheader.json` file alongside the `.ots` proof. |

Anchors file hashes in the Bitcoin blockchain via [OpenTimestamps](https://opentimestamps.org/). The `.ots` file initially contains a **pending proof**. After Bitcoin confirmation (~hours), it can be **upgraded** to a full proof via `zp jobs run`.

The module automatically schedules an **async job** for upgrading. The job uses `retry_max: null` (unlimited retries) and a default `retry_interval` of `1h`.

---

### `main_branch` / `github`

```yaml
main_branch: main
github:
  check_draft: false
```

| Option | Value | Effect |
|--------|-------|--------|
| `main_branch` | `main` | The pipeline checks that you are on this branch and that it is synced with the remote. |
| `github.check_draft` | `false` | Skip draft release detection. When `true`, ZP scans all releases via the REST API to prevent accidentally converting a draft to a published release. Disabled by default because it requires paginating all releases (slower). |

---

### `compile`

```yaml
compile:
  enabled: true
  dir: papers/latex
```

| Option | Value | Effect |
|--------|-------|--------|
| `enabled` | `true` | Runs `make deploy` in the compile directory at pipeline step 5. |
| `dir` | `papers/latex` | Relative path from project root to the directory containing the Makefile. |

**What happens**: the pipeline runs `make deploy` in `<project_root>/papers/latex/` with `ZP_*` environment variables (commit SHA, date, tag, author, etc.) merged into the process environment. This typically compiles `main.tex` into `main.pdf`.

The `{compile_dir}` template variable in patterns resolves to this value (`papers/latex`).

If `enabled: false`, the pipeline skips compilation but still validates that `dir` exists (if set).

---

### `archive`

```yaml
archive:
  format: zip
  dir: papers/latex/releases
  types: [file, sig]
```

| Option | Value | Effect |
|--------|-------|--------|
| `format` | `zip` | Archive format for the `project` entry. Also available: `tar` (reproducible), `tar.gz` (reproducible + compressed). ZIP is not byte-reproducible across runs. |
| `dir` | `papers/latex/releases` | Persistent archive directory. After publishing, files are copied to `<dir>/<tag_name>/`. If `null`, files are only in the temporary working directory and are lost after the run. |
| `types` | `[file, sig]` | **Global default** for which file types to persist to `archive.dir`. `file` = the file itself, `sig` = its GPG signature. Module names (e.g. `digicert_timestamp`) can be added to also persist module outputs. Per-file `archive_types` overrides this. |

**Result with tag `v1.0.0`**: files are persisted to `papers/latex/releases/v1.0.0/`. By default, each file and its signature are persisted — unless the per-file `archive_types` overrides this.

---

### `hash_algorithms` / `identity_hash_algo` / `identity_key`

```yaml
hash_algorithms: [md5, sha256, tree]
identity_hash_algo: sha256
identity_key: hash
```

#### `hash_algorithms`

List of hash algorithms computed for every file in the pipeline.

| Algorithm | Purpose |
|-----------|---------|
| `md5` | Matches Zenodo's default file checksum — allows comparison without re-downloading. |
| `sha256` | Cryptographically secure hash for integrity verification. |
| `tree` | Git tree hash (SHA-1) — depends only on file content, not the archive format. Provides a reproducible content identifier independent of ZIP/TAR parameters. For non-archive files (like PDFs), falls back to `sha1`. |

Also available: `sha1`, `sha512`, `tree256` (SHA-256 git tree hash).

#### `identity_hash_algo`

A **single global algorithm** used consistently across several roles:

1. **Identity hash**: every file gets `sha256:<hex>` as its canonical fingerprint (`FileEntry.external_identifier`)
2. **`publish_identity_hash`**: this is the value published — as `.identity_hash.txt` on GitHub or `zp:///` alternate identifier on Zenodo
3. **GPG signing in `file_hash` mode**: GPG would sign this hash string (not used here since `sign_mode: file`)
4. **Modules**: passed as `config.identity_hash_algo` in the module input JSON, so modules use the same algorithm for their hash computation

**Intentionally global** (no per-file override): all roles must use the same algorithm so that identifiers, signatures, and module certifications are comparable across entries.

#### `identity_key`

Controls the format of `zp:///` Zenodo alternate identifiers and the key field in manifest entries.

| `identity_key` | Zenodo identifier | Manifest file entry |
|---|---|---|
| `name` (default) | `zp:///<filename>;sha256:<hex>` | `{"key": "paper.pdf", ...}` |
| **`hash`** (this config) | `zp:///sha256:<hex>` | `{"identity_hash": "sha256:abc...", ...}` |

With `identity_key: hash`, identifiers are pure content hashes — no filename is embedded. This makes them stable across renames and easier to verify independently.

---

### `signing`

```yaml
signing:
  sign: true
  sign_mode: file
```

| Option | Value | Effect |
|--------|-------|--------|
| `sign` | `true` | Enable GPG signing globally. Every file without an explicit `sign: false` override will be signed. |
| `sign_mode` | `file` | Sign the file directly with GPG (detached signature). Produces `.asc` files (ASCII-armored by default). |

**Two signing modes**:
- **`file`** (this config): `gpg --detach-sign --armor <file>` → produces `<file>.asc`
- **`file_hash`**: computes the file's `identity_hash_algo` hash, writes `sha256:<hex>` to a temp file, signs that → produces `<file>.sha256.asc`

The `file` mode is simpler: the signature is directly verifiable against the original file with `gpg --verify file.pdf.asc file.pdf`.

**GPG key**: uses the default GPG key from your keyring. Can be overridden with `gpg.uid` and `gpg.extra_args`.

**Per-file override**: each `generated_files` entry can set `sign: true/false` and `sign_mode: file/file_hash` independently. CLI flags `--sign`/`--no-sign` override the global setting only, not per-file overrides.

---

### `zenodo`

```yaml
zenodo:
  api_url: "https://sandbox.zenodo.org/api"
  concept_doi: "153267"
```

| Option | Value | Effect |
|--------|-------|--------|
| `api_url` | `https://sandbox.zenodo.org/api` | Zenodo sandbox for testing. Use `https://zenodo.org/api` for production. |
| `concept_doi` | `153267` | The concept DOI of the deposit (shared across all versions). Obtained after manually creating the first version on Zenodo. |

Also available:
- `publication_date`: override the publication date (default: today UTC)
- `force_update`: force upload even if files are identical to the latest Zenodo version

The Zenodo API token is configured separately in `.zenodo.env` (not in `.zp.yaml`):
```env
ZENODO_TOKEN=your_token
```

---

### `generated_files`

This is the core of the configuration. Each key declares a file to include in the release, its signing behavior, where it is published, and whether modules process it.

Three types of entries:
- **Pattern entries** (custom key, e.g. `paper`): files matched by glob pattern
- **`project`** (reserved key): a git archive of the repository
- **`manifest`** (reserved key): an auto-generated JSON manifest

---

#### `paper` — compiled PDF

```yaml
  paper:
    pattern: "{compile_dir}/main.pdf"
    rename: true
    publishers:
      destination:
        file: [github, zenodo]
        sig: [github]
    publish_identity_hash:
      destination:
        file: [github, zenodo]
        sig: [github]
```

##### What it does

| Option | Value | Effect |
|--------|-------|--------|
| `pattern` | `"{compile_dir}/main.pdf"` | Matches `papers/latex/main.pdf` (since `compile.dir = papers/latex`). The `{compile_dir}` template is resolved at config time. |
| `rename` | `true` | Renames the matched file to `MyProject-v1.0.0.pdf` (project name + original extension). |
| `publishers.destination.file` | `[github, zenodo]` | Upload the PDF to both GitHub release and Zenodo deposit. |
| `publishers.destination.sig` | `[github]` | Upload the GPG signature (`.asc`) to GitHub only. |
| `publish_identity_hash.destination.file` | `[github, zenodo]` | Publish the PDF's identity hash to both platforms. |
| `publish_identity_hash.destination.sig` | `[github]` | Publish the signature's identity hash to GitHub. |

##### Inherited settings (from global config)
- `sign`: `true` (from `signing.sign`) — the PDF will be signed
- `sign_mode`: `file` (from `signing.sign_mode`) — direct file signature
- `archive_types`: `[file, sig]` (from `archive.types`) — both PDF and `.asc` are persisted to archive dir

##### Files produced

| File | Type | Description |
|------|------|-------------|
| `MyProject-v1.0.0.pdf` | file | Compiled PDF, renamed |
| `gpg_sign/MyProject-v1.0.0.pdf.asc` | sig | Detached GPG signature of the PDF |

##### Where each file ends up

| File | GitHub | Zenodo | Disk (`releases/v1.0.0/`) |
|------|--------|--------|---------------------------|
| `MyProject-v1.0.0.pdf` | uploaded | uploaded | `MyProject-v1.0.0.pdf` |
| `MyProject-v1.0.0.pdf.asc` | uploaded | — | `gpg_sign/MyProject-v1.0.0.pdf.asc` |
| `MyProject-v1.0.0.pdf.identity_hash.txt` | uploaded | — | — |
| identity hash `zp:///sha256:<hex>` | — | added as alternate identifier | — |
| `MyProject-v1.0.0.pdf.asc.identity_hash.txt` | uploaded | — | — |

---

#### `project` — git archive

```yaml
  project:
    archive_types: []
    publishers:
      destination:
        file: [zenodo]
```

##### What it does

| Option | Value | Effect |
|--------|-------|--------|
| `archive_types` | `[]` | **Nothing** is persisted to disk. The archive only lives in the temporary working directory and is uploaded, then discarded. |
| `publishers.destination.file` | `[zenodo]` | Upload the ZIP to Zenodo only. |

##### Inherited settings
- `rename`: `false` (default) — uses the repository directory name as archive prefix (e.g. `my-repo.zip`)
- `sign`: `true` (from global) — the ZIP is signed, but there is no `destination.sig` so the signature is not uploaded anywhere
- No `publish_identity_hash` — identity hash is not published

##### Files produced

| File | Type | Description |
|------|------|-------------|
| `my-repo.zip` | project | Git archive (ZIP) of the repository at the release tag |
| `gpg_sign/my-repo.zip.asc` | sig | GPG signature (created but not published) |

##### Where each file ends up

| File | GitHub | Zenodo | Disk |
|------|--------|--------|------|
| `my-repo.zip` | — | uploaded | — (`archive_types: []`) |
| `my-repo.zip.asc` | — | — | — (`archive_types: []`) |

> **Note**: the signature is computed (because global `sign: true`) but goes nowhere — it is neither published (no `destination.sig`) nor persisted (no `sig` in `archive_types`). To avoid this, you can set `sign: false` on the project entry, or add `sig: [zenodo]` to its publishers.

---

#### `manifest` — JSON manifest with modules

```yaml
  manifest:
    archive_types: [file, digicert_timestamp, ots_timestamp]
    commit_info: [sha, date_epoch]
    sign: true
    content:
      paper: [file]
      project: [file]
    publish_identity_hash:
      destination:
        file: [github, zenodo]
    publishers:
      destination:
        file: [github, zenodo]
        digicert_timestamp: [github]
        ots_timestamp: [github]
    modules:
      digicert_timestamp: {}
      ots_timestamp:
        input_types: [file, digicert_timestamp]
```

This is the most complex entry. It generates a JSON manifest, signs it, timestamps it with two modules, and publishes everything to different platforms.

##### Manifest content

```yaml
    content:
      paper: [file]
      project: [file]
    commit_info: [sha, date_epoch]
```

| Option | Value | Effect |
|--------|-------|--------|
| `content` | `paper: [file], project: [file]` | Include hashes of the paper PDF and project ZIP in the manifest. `[file]` means include the file itself (not its signature). Add `sig` to include signature hashes too. |
| `commit_info` | `[sha, date_epoch]` | Include the commit SHA and Unix epoch timestamp in the manifest. Also available: `subject`, `author_name`, `author_email`, `branch`, `origin`. |

The manifest is serialized as [JCS (RFC 8785)](https://www.rfc-editor.org/rfc/rfc8785) canonical JSON — deterministic byte output, so the same content always produces the same hash.

**Generated file**: `manifest-v1.0.0.json` (suffix from `project_name.suffix`).

Example output:
```json
{
  "commit": {"date_epoch": 1234567890, "sha": "abc123..."},
  "files": [
    {"identity_hash": "sha256:def456...", "md5": "...", "sha256": "...", "tree": "..."},
    {"identity_hash": "sha256:789abc...", "md5": "...", "sha256": "...", "tree": "..."}
  ],
  "identity_hash_algo": "sha256",
  "version": {"label": "v1.0.0", "sha": "tag_object_sha"}
}
```

Note: `identity_key: hash` controls the file entry format — `"identity_hash": "sha256:..."` instead of `"key": "filename.pdf"`.

##### Signing

```yaml
    sign: true
```

Explicit per-file override (same as global here, but makes intent clear). Produces `manifest-v1.0.0.json.asc` (since global `sign_mode: file`).

##### Modules

```yaml
    modules:
      digicert_timestamp: {}
      ots_timestamp:
        input_types: [file, digicert_timestamp]
```

Modules attached to this entry. They run at pipeline step 12, **after** signing.

**`digicert_timestamp: {}`**: uses the global module config (`full_chain: true`). No per-file override. Processes the manifest file and produces a `.tsr` timestamp response.

**`ots_timestamp`** with `input_types: [file, digicert_timestamp]`:
- `file` → processes the manifest file itself → produces `manifest-v1.0.0.json.ots`
- `digicert_timestamp` → also processes the `.tsr` from digicert → produces `manifest-v1.0.0.json.tsr.ots`

This creates a **chain of trust**: the manifest is timestamped by both DigiCert (instant, centralized) and OpenTimestamps (delayed, decentralized). The OTS module also timestamps the DigiCert `.tsr` itself, anchoring the centralized timestamp proof in the blockchain.

Without `input_types`, the OTS module would only process the manifest file (default: all files except signatures).

##### Publishers

```yaml
    publishers:
      destination:
        file: [github, zenodo]
        digicert_timestamp: [github]
        ots_timestamp: [github]
```

Each key in `destination` routes a type of file to specific platforms:

| Key | Matches | Destination |
|-----|---------|-------------|
| `file` | The manifest JSON itself | GitHub + Zenodo |
| `digicert_timestamp` | All outputs from `digicert_timestamp` module (`.tsr`) | GitHub only |
| `ots_timestamp` | All outputs from `ots_timestamp` module (`.ots`) | GitHub only |

> **Note**: there is no `sig` key, so the manifest's GPG signature (`manifest-v1.0.0.json.asc`) is **not uploaded** to any platform. It exists only in the working directory during the run. To upload it, add `sig: [github]` or `sig: [zenodo]`.

##### Persistence

```yaml
    archive_types: [file, digicert_timestamp, ots_timestamp]
```

Controls which file types are copied to `papers/latex/releases/v1.0.0/`:

| Type key | What is persisted |
|----------|-------------------|
| `file` | `manifest-v1.0.0.json` |
| `digicert_timestamp` | `digicert_timestamp/manifest-v1.0.0.json.tsr` |
| `ots_timestamp` | `ots_timestamp/manifest-v1.0.0.json.ots` and `ots_timestamp/manifest-v1.0.0.json.tsr.ots` |

Note: `sig` is **absent** from `archive_types`, so the GPG signature is not persisted to disk.

##### All files produced by the manifest entry

| File | Type | Source |
|------|------|--------|
| `manifest-v1.0.0.json` | manifest | Generated by ZP (JCS) |
| `gpg_sign/manifest-v1.0.0.json.asc` | sig | GPG signing |
| `digicert_timestamp/manifest-v1.0.0.json.tsr` | module | DigiCert TSA |
| `ots_timestamp/manifest-v1.0.0.json.ots` | module | OTS (stamp of manifest) |
| `ots_timestamp/manifest-v1.0.0.json.tsr.ots` | module | OTS (stamp of DigiCert .tsr) |

##### Where each file ends up

| File | GitHub | Zenodo | Disk (`releases/v1.0.0/`) |
|------|--------|--------|---------------------------|
| `manifest-v1.0.0.json` | uploaded | uploaded | `manifest-v1.0.0.json` |
| `manifest-v1.0.0.json.asc` | — | — | — |
| `manifest-v1.0.0.json.tsr` | uploaded | — | `digicert_timestamp/manifest-v1.0.0.json.tsr` |
| `manifest-v1.0.0.json.ots` | uploaded | — | `ots_timestamp/manifest-v1.0.0.json.ots` |
| `manifest-v1.0.0.json.tsr.ots` | uploaded | — | `ots_timestamp/manifest-v1.0.0.json.tsr.ots` |
| `manifest-v1.0.0.json.identity_hash.txt` | uploaded | — | — |
| identity hash `zp:///sha256:<hex>` | — | added as alternate identifier | — |

---

### `prompt_validation_level`

```yaml
prompt_validation_level: danger
```

Controls how much confirmation is required at interactive prompts.

| Level | Behavior | Use case |
|-------|----------|----------|
| **`danger`** (this config) | Just press **Enter** | Fast iteration during development or testing |
| `light` | Type `y` or `yes` | Default for most workflows |
| `normal` | Type the full word `yes` | Extra caution for production |
| `secure` | Type the **project root directory name** | Maximum protection against wrong-project mistakes |

---

## Complete output summary

With this configuration and tag `v1.0.0`, here is the complete picture of what the pipeline produces.

### On disk — `papers/latex/releases/v1.0.0/`

```
papers/latex/releases/v1.0.0/
├── MyProject-v1.0.0.pdf
├── gpg_sign/
│   └── MyProject-v1.0.0.pdf.asc
├── manifest-v1.0.0.json
├── digicert_timestamp/
│   └── manifest-v1.0.0.json.tsr
└── ots_timestamp/
    ├── manifest-v1.0.0.json.ots
    └── manifest-v1.0.0.json.tsr.ots
```

Note: the project ZIP and manifest signature are **not** on disk (respectively `archive_types: []` and `sig` absent from `archive_types`).

### On GitHub release `v1.0.0`

```
Assets:
  MyProject-v1.0.0.pdf                          (paper)
  MyProject-v1.0.0.pdf.asc                      (paper GPG signature)
  MyProject-v1.0.0.pdf.identity_hash.txt         (paper identity hash)
  MyProject-v1.0.0.pdf.asc.identity_hash.txt     (paper sig identity hash)
  manifest-v1.0.0.json                           (manifest)
  manifest-v1.0.0.json.identity_hash.txt          (manifest identity hash)
  manifest-v1.0.0.json.tsr                       (DigiCert timestamp)
  manifest-v1.0.0.json.ots                       (OTS proof — pending)
  manifest-v1.0.0.json.tsr.ots                   (OTS proof of DigiCert .tsr — pending)
```

### On Zenodo deposit

```
Files:
  MyProject-v1.0.0.pdf                           (paper)
  my-repo.zip                                    (project archive)
  manifest-v1.0.0.json                           (manifest)

Metadata:
  version: v1.0.0
  publication_date: <today UTC>
  identifiers:
    - {"scheme": "other", "identifier": "zp:///sha256:<paper_hash>"}
    - {"scheme": "other", "identifier": "zp:///sha256:<manifest_hash>"}
```

### Async jobs — `~/.zp/jobs/`

The `ots_timestamp` module creates an async job for upgrading the pending OTS proofs:

```
ID          MODULE               TAG          STATUS     RETRIES  NEXT IN    DESCRIPTION
a3f1b29c    ots_timestamp        v1.0.0       pending    0        ready      Upgrade pending OTS proofs
```

Run `zp jobs run` after Bitcoin confirmation (~hours) to upgrade the proofs. On success, the `.ots` files in `papers/latex/releases/v1.0.0/ots_timestamp/` are updated from pending to Bitcoin-attested.

---

## Pipeline step trace

Here is what happens at each pipeline step with this configuration:

| Step | What happens |
|------|-------------|
| **1. Git check** | Verifies you're on `main` branch, no uncommitted changes, no unpushed commits/tags, remote is up-to-date. |
| **2. Release** | Checks if the latest commit has a release. If not, prompts for tag name, title, notes. Creates the GitHub release via `gh release create`. |
| **3. Commit info** | Extracts SHA, timestamp, author, etc. from the tagged commit. Sets `ZP_*` environment variables. |
| **4. Project name** | Resolves `MyProject` + `-v1.0.0` → `MyProject-v1.0.0`. |
| **5. Compile** | Runs `make deploy` in `papers/latex/` with `ZP_*` vars. Produces `main.pdf`. |
| **6. Re-check** | Re-verifies git state and release after compilation. |
| **7. Resolve files** | Matches `papers/latex/main.pdf` for the `paper` entry. |
| **8. Archive** | Copies `main.pdf` → `MyProject-v1.0.0.pdf` (rename). Creates `my-repo.zip` via `git archive`. |
| **9. Compute hashes** | Computes md5, sha256, tree for all files. |
| **10. Manifest** | Generates `manifest-v1.0.0.json` with paper + project hashes, commit SHA, date. Then hashes the manifest itself. |
| **11. Sign** | Signs paper PDF, project ZIP, and manifest with GPG (`file` mode → `.asc`). |
| **12. Modules** | (a) `digicert_timestamp` stamps the manifest → `.tsr`. (b) `ots_timestamp` stamps manifest + `.tsr` → two `.ots` files. Schedules async job. |
| **13. Publish** | Uploads files to GitHub and Zenodo per `publishers` config. Adds `zp:///` identifiers to Zenodo. Creates `.identity_hash.txt` files for GitHub. |
| **14. Persist** | Copies files to `papers/latex/releases/v1.0.0/` per `archive_types` config. |

---

## Config option reference

For quick lookup. See the [main README](./README.md) for full explanations.

### Root-level options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `project_name.prefix` | str | `""` (dir name) | Base name for file renaming |
| `project_name.suffix` | str | `"-{tag_name}"` | Suffix with template variables |
| `main_branch` | str | `main` | Branch the pipeline checks |
| `debug` | bool | `false` | Enable debug output |
| `hash_algorithms` | list | `[]` | Hash algorithms to compute per file |
| `identity_hash_algo` | str | `sha256` | Canonical hash for identifiers, signing, modules |
| `identity_key` | str | `name` | `"name"` or `"hash"` — controls `zp:///` format |
| `prompt_validation_level` | str | `light` | Prompt confirmation level |

### `compile`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `compile.enabled` | bool | `true` | Run `make deploy` |
| `compile.dir` | str | `""` (project root) | Directory containing the Makefile |
| `compile.make_args` | list | `[]` | Extra args for `make` |

### `archive`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `archive.format` | str | `zip` | `zip`, `tar`, or `tar.gz` |
| `archive.dir` | str | `null` | Persistent archive directory |
| `archive.types` | list | `[file, sig]` | Default file types to persist |
| `archive.tar_extra_args` | list | (defaults) | Override reproducible tar args |
| `archive.gzip_extra_args` | list | (defaults) | Override gzip args |

### `signing`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `signing.sign` | bool | `false` | Enable GPG signing globally |
| `signing.sign_mode` | str | `file_hash` | `file` or `file_hash` |
| `signing.gpg.uid` | str | `null` | GPG key UID (null = default key) |
| `signing.gpg.extra_args` | list | `["--armor"]` | GPG extra args |

### `zenodo`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `zenodo.api_url` | str | `https://zenodo.org/api` | Zenodo API URL |
| `zenodo.concept_doi` | str | `""` | Concept DOI of the deposit |
| `zenodo.publication_date` | str | `null` (today) | YYYY-MM-DD |
| `zenodo.force_update` | bool | `false` | Force upload even if identical |

### `github`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `github.check_draft` | bool | `false` | Scan for draft releases before creating |

### `generated_files` per-entry options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `pattern` | str | — | Glob pattern (pattern entries only) |
| `rename` | bool | `false` | Rename using project name template |
| `sign` | bool | `null` | Override global `signing.sign` |
| `sign_mode` | str | `null` | Override global `signing.sign_mode` |
| `archive_types` | list | `null` (global) | File types to persist (`[]` = nothing) |
| `publishers.destination.<type>` | list | global default | Where to upload each file type |
| `publish_identity_hash.destination.<type>` | list | — | Where to publish identity hashes |
| `modules.<name>` | dict | — | Per-file module config override |

### `manifest`-specific options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `content` | dict | `null` (all) | Which entries/types to include: `{key: [type_keys]}` |
| `commit_info` | list | `[]` | Commit fields: `sha`, `date_epoch`, `subject`, `author_name`, etc. |
| `zenodo_metadata` | list | `[]` | Metadata fields from `.zenodo.json`: `title`, `creators`, etc. |
