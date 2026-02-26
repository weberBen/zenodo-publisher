# Zenodo Publisher

A lightweight local CLI tool for publishing LaTeX and compiled projects to [Zenodo](https://zenodo.org/) from git repo. Designed for frequent, rapid releases by a singlish maintainer.

![demo.gif](https://raw.githubusercontent.com/weberBen/zenodo-publisher/refs/heads/assets/assets/demo.gif)

## Why This Tool?

GitHub Actions like [rseng/zenodo-release](https://github.com/rseng/zenodo-release) or [megasanjay/upload-to-zenodo](https://github.com/megasanjay/upload-to-zenodo) work well for collaborative projects, but we wanted:

- **Local control**: No isolated CI environment - everything runs locally where your LaTeX setup already works
- **Predictable timing**: No cache invalidation delays that sometimes slow down GitHub Actions unpredictably. Even if, timing depends on the API itself, but no more middleman
- **Step-by-step feedback**: Console output shows exactly what's happening at each stage
- **Singlish maintainer workflow**: Optimized for "one" person handling releases while others contribute code

This tool is **not recommended** for highly collaborative projects where multiple people need to trigger releases. For that, use GitHub Actions.

# Publication workflow

```mermaid
graph TD
    Z[⚙️ Manual git sync] -.->|start tool| A
    A[📄 Compile Doc] -->|PDF/other generated| B{🔄 Git sync check}
    B -->|Local ≠ Remote| C[⚠️ Pull/Push required <br/> Manual]
    B -->|Local = Remote| D{🏷️ Release exists? <br/> GitHub CLI}
    C --> D
    D -->|No| E[✨ Create release + tag]
    D -->|Yes| F[📦 Create archive]
    E --> F
    F -->|PDF and/or optional ZIP| F2{🔏 GPG Sign?}
    F2 -->|Yes| F3[🔏 Sign files]
    F2 -->|No| G{📚 Check Zenodo}
    F3 --> G{📚 Check Zenodo}
    G --> H{🔐 Files equal? <br/> md5 sum}
    H -->|Yes| I{🏷️ Versions equal?}
    H -->|No| J{🏷️ Versions equal?}
    I -->|Yes| K[✅ Skip publication <br/> identical]
    I -->|No| L[✅ Skip publication <br/>⚠️ Warning]
    J -->|Yes| M[⬆️ Publish <br/>⚠️ Warning]
    J -->|No| N[⬆️ Publish <br/> All different]
    K --> O{🔄 Force?}
    L --> O
    O -->|Yes| P[⬆️ Upload to Zenodo]
    O -->|No| Q[✅ Skip publication]
    M --> P
    N --> P
    P --> R[🎉 Publish on Zenodo <br\> InvenioRDM API]
    
    style Z fill:#f0f0f0,stroke-dasharray: 5 5
    style A fill:#e1f5ff
    style E fill:#fff4e1
    style R fill:#e8f5e9
    style Q fill:#f3e5f5
    style K fill:#f3e5f5
    style L fill:#fff3cd
    style C fill:#ffe0e0
    style H fill:#fff9e1
    style I fill:#fff9e1
    style J fill:#fff9e1
    style M fill:#ffd6cc
    style N fill:#e8f5e9
    style O fill:#ffe4cc
```

You can publish only the zip of the project or add a dynamic compilation (through makefile) to include another file on the zenodo repo (but not included in the git repo). Typically, project having both source paper and source code inside a single git repo but does not want to have the compiled pdf file in the git tree.

## Prerequisites

- **Python 3.10+**
- **uv** (Python package manager): https://docs.astral.sh/uv/
- **GitHub CLI** (`gh`): https://cli.github.com/ - used for creating GitHub releases
- **Existing Zenodo deposit**: The script creates new versions, not new deposits. You must manually create the first version on Zenodo.
- **GnuPG** (optional): Required only if `GPG_SIGN=True`. Must have at least one secret key in your keyring.
- If using **LaTeX distribution**, prefer using `latexmk` as it handles citation/reference as error. But we can use whatever you want.

## Installation

```bash
# Clone or copy this tool somewhere
git clone <repo-url> zenodo-publisher
cd zenodo-publisher
```

Install the tool globally

```bash
# Or install globally
uv tool install .
```

Or locally by running the `bash` launcher
```bash
uv sync
```

Then
```bash
chmod +x zp.bash
```

## Usage

```bash
# From your project directory (where .zenodo.env is located)
zp
# or zp.bash or any symlink to the bash launcher if the tool is not installed globally
zp --help
```

Then use the script at the root of your project.

You have a functionning example of such a project repo [here](https://github.com/weberBen/zenodo-sandbox-publisher). See the associated readme for instruction.


## Project Setup

### 1. Create `.zenodo.env` in your project root

#### Configuration Options


| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PROJECT_NAME` | No | Root dir name | Project name for display and file naming (e.g., `MyProject-v1.0.0.pdf`) |
| `MAIN_BRANCH` | No | `main` | Branch to check for releases |
| `COMPILE_DIR` | Yes | - | Path to compilation directory (relative to project root) |
| `MAIN_FILE` | No | `main.pdf` | Main source file (with extension) renamed as `<PROJECT_NAME>-<version_tag_name>.<extension>` and used as the default preview by Zenodo |
| `COMPILE` | No | `True` | Let the script compile project through `Makefile` |
| `MAKE_ARGS` | No | - | Extra args passed to make (comma-separated, e.g. `-j4,VERBOSE=1`) |
| `PUBLISHER_TYPE` | Yes | - | Set to `zenodo` to enable publishing |
| `ZENODO_TOKEN` | Yes | - | Your Zenodo API token |
| `ZENODO_CONCEPT_DOI` | Yes | - | Concept DOI of your Zenodo deposit |
| `ZENODO_API_URL` | No | `https://zenodo.org/api` | Use `https://sandbox.zenodo.org/api` for testing |
| `ARCHIVE_TYPES` | No | `project` (zip file) | What to archive: `<extension>`, `project`, or `pdf,project` |
| `PERSIST_TYPES` | No | - | What to save to `ARCHIVE_DIR` (rest goes to temporary dir) |
| `ARCHIVE_DIR` | No | - | Directory to save persistent archives |
| `PUBLICATION_DATE` | No | Current UTC date | Publication date (format ISO YYYY-MM-DD) |
| `ZENODO_INFO_TO_RELEASE` | No | `False` | Add zenodo publication info (DOI, URL, checksums) as a GitHub release asset |
| `ZENODO_IDENTIFIER_HASH` | No | `False` | Add SHA256 hash as alternate identifier in Zenodo metadata |
| `ZENODO_IDENTIFIER_TYPES` | No | - | File types to include in identifier hash (e.g. `pdf`, `project`, `pdf,project`). If multiple, hashes are concatenated and re-hashed |
| `DEBUG` | No | `False` | Enable debug mode (shows full stack traces on errors) |
| `PROMPT_VALIDATION_LEVEL` | No | `strict` | Prompt validation level: `strict` (type project name) or `light` (y/n) |
| `ZENODO_FORCE_UPDATE` | No | `False` | Force Zenodo update even if already up to date |
| `GPG_SIGN` | No | `False` | Enable GPG signing of archived files before upload |
| `GPG_UID` | No | - | GPG key UID to use for signing (empty = system default key) |
| `GPG_OVERWRITE` | No | `False` | Overwrite existing signature files without prompting |
| `GPG_EXTRA_ARGS` | No | `--armor` | Extra args passed to gpg (comma-separated). E.g. use `--no-armor` for binary `.sig` |

See example file [here](./zenodo.env.example).

And create a Zenodo token on `account/settings/applications/tokens/new/` (token created on [Zenodo sandbox](https://sandbox.zenodo.org/) are dissociated from production) and allow `deposit:actions`and `deposit:write`.

##### Notes

- Latex is optional.
- If your project include no latex at all, and you're not interested in pdf archive and/or dynamic compilation, you can set `COMPILE=False`, `COMPILE_DIR=`, `ARCHIVE_TYPES=project`.
- If you want to include a simple file (non latex based), set the `COMPILE_DIR` and the `MAIN_FILE`. The script will look for your file at `<COMPILE_DIR>/<MAIN_FILE>`. And also set `COMPILE=False`, `ARCHIVE_TYPES=<my_file_extension>,project`.

### 2. Create a Makefile in your compile directory

The script calls `make deploy` in the directory specified by `COMPILE_DIR`. Your Makefile must have a `deploy` target:

```makefile
.PHONY: deploy
deploy: cleanall all
```

See `Makefile.example` for a complete template.
For latex project , we recommand doing a deep clean (including the pdf) on the deploy action to handle possible outdated version artifact.
But if your base compile time is too long, you can skip the clean, which will use your already compiled file.
You can also disable the compile `COMPILE=False` but be aware that in case of missing compiled file, the script will raise exception.

#### Environment variables passed to `make`

The script passes the following environment variables to `make deploy`, containing information about the commit being released:

| Variable | Description |
|----------|-------------|
| `ZP_COMMIT_DATE_EPOCH` | Unix epoch timestamp of the commit |
| `ZP_COMMIT_SHA` | Full SHA hash of the commit |
| `ZP_COMMIT_TAG` | Tag name (set by the pipeline to the release tag) |
| `ZP_COMMIT_SUBJECT` | Commit message subject line |
| `ZP_BRANCH` | Branch name (set by the pipeline to the main branch) |
| `ZP_COMMIT_COMMITTER_NAME` | Name of the committer |
| `ZP_COMMIT_COMMITTER_EMAIL` | Email of the committer |
| `ZP_COMMIT_AUTHOR_NAME` | Name of the author |
| `ZP_COMMIT_AUTHOR_EMAIL` | Email of the author |

All variables are prefixed with `ZP_` to avoid collisions with git's own environment variables (e.g. `GIT_AUTHOR_NAME`).

##### Reproducible PDFs with `SOURCE_DATE_EPOCH`

For LaTeX projects, you can set `SOURCE_DATE_EPOCH` from `ZP_COMMIT_DATE_EPOCH` at the top of your Makefile:

```makefile
ifdef ZP_COMMIT_DATE_EPOCH
export SOURCE_DATE_EPOCH ?= $(ZP_COMMIT_DATE_EPOCH)
endif
```

When `SOURCE_DATE_EPOCH` is set, pdflatex/lualatex automatically use this date for `\today` and PDF metadata instead of the current time. This makes the PDF reproducible: running the script twice on the same commit produces the same PDF with the same MD5 checksum.

### 3. Configure LaTeX for reproducible PDFs

For MD5 checksum comparison to work correctly, your PDFs must be reproducible. Add these lines to your `.tex` file:

```latex
\pdfinfoomitdate=1
\pdfsuppressptexinfo=-1
\pdftrailerid{}
```

This is highly recommanded, not mandatory, but without theses the only reference point between the git repo and the zenodo repo will be the version tag name. With this option, we can also compare the content of the previous version to make sure files are different. In theory, the version tag is in sync with the git one, but since we can manually edit theses, a backup solution is always appreciated.

## How It Works

### 1. Build LaTeX
Runs `make deploy` in `COMPILE_DIR`. The script **stops on any error**.

### 2. Git Checks
- Verifies you're on `MAIN_BRANCH`
- Checks branch is up-to-date with remote (no unpushed/unpulled commits)
- Checks no local modifications exist

This tool use `git fetch` (not in dry run mode). Thus if it's a problem to fetch regularly in your project, do not use the tool.

### 3. Tag Validation
- If tag doesn't exist: proceeds
- If tag exists: verifies it points to the latest commit on the remote branch

### 4. GitHub Release
Creates a GitHub release using `gh release create` ([GitHub CLI](https://cli.github.com/)). This automatically creates and pushes the tag.

### 5. Archive & Upload
- Creates file archive (and optionally project ZIP)
- The project ZIP uses `git archive` ( ≈ same as GitHub's ZIP), so untracked local files are excluded

### 5b. GPG Signing (optional)
- If `GPG_SIGN=True`, signs each archived file with a detached GPG signature
- Verifies each signature after creation
- Signature files (`.asc`/`.sig`) follow the same persist/temp rules as the signed files
- Signature files are excluded from Zenodo MD5 comparison (timestamps make them non-reproducible)

### 7. Zenodo Checks
- Verifies the version doesn't already exist on Zenodo
- Compares file checksums (MD5) and version names to detect changes

### 8. Zenodo Publishing
- Uploads files to Zenodo
- Update metadata
- Publish ([InvenioRDM API](https://inveniordm.docs.cern.ch/))

## Limitations

### Edge cases

Application mostly vibe coded (though verified, especially the Zenodo operations and archive operations), not optimized, not really clean, but working and tested multiple times at each steps on different cases in sandbox, nonetheless be sure to test it on your specific usage before using it in production (test it with a test git repo, and a [Zenodo sandbox](https://sandbox.zenodo.org/) deposit)

### Test on Sandbox First
Always test with `ZENODO_API_URL=https://sandbox.zenodo.org/api` before using production. The script doesn't handle all edge cases.

### Draft Handling
The script **discards existing drafts** on the Zenodo identified deposit by the concept DOI. If you're collaborating on Zenodo through the web interface while using this script, drafts may be lost.

### Zenodo metadata
- Metadata is copied from the previous version. Only `version` and `publication_date` are modified.
- Each version gets a new DOI (no custom DOI per release)

### First Version Required
You must manually create the first version on Zenodo before using this script. It only creates new versions of existing deposits.

### PDF Storage Philosophy
This tool assumes you **don't store PDFs/compiled files in git**. PDFs are generated on-the-fly before upload. If your PDFs are already in the repository, consider using Zenodo's native GitHub integration instead.

## Troubleshooting

### "Project not initialized for Zenodo publisher"
Create a `.zenodo.env` file in your project root.

### "Compile directory not found"
Check that `COMPILE_DIR` points to a valid directory containing your Makefile.
And check that `COMPILE=True` or `COMPILE=`.

### "Files are identical/different to version X on Zenodo"
Your PDF hasn't changed. This usually means:
- You forgot to add the reproducible PDF settings
- Build artifacts from a previous build affected the output
- Version tag is the same, so the check update halt here
- Version tag is different but files content is the same so proceeded with new version

### "Files are different" but project hasn't changed
The script detects file differences (MD5) between local archives and Zenodo even though the git project hasn't changed. This is typically caused by non-reproducible PDFs: each compilation embeds the current date/time, producing a different checksum every run.

**Solution**: Set `SOURCE_DATE_EPOCH` from `ZP_COMMIT_DATE_EPOCH` at the top of your Makefile.

This locks `\today` and PDF metadata to the commit date, making the PDF identical across runs. Also make sure you have the [reproducible PDF settings](#3-configure-latex-for-reproducible-pdfs) in your `.tex` file.

### GitHub CLI errors
Make sure `gh` is installed and authenticated: `gh auth login`

## To do

- [ ] Integrate `.zenodo.json` file for richer metadata update
