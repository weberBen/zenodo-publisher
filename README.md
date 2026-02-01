# Zenodo Publisher

A lightweight local CLI tool for publishing LaTeX projects to [Zenodo](https://zenodo.org/) from git repo. Designed for frequent, rapid releases by a singlish maintainer.

## Why This Tool?

GitHub Actions like [rseng/zenodo-release](https://github.com/rseng/zenodo-release) or [megasanjay/upload-to-zenodo](https://github.com/megasanjay/upload-to-zenodo) work well for collaborative projects, but we wanted:

- **Local control**: No isolated CI environment - everything runs locally where your LaTeX setup already works
- **Predictable timing**: No cache invalidation delays that sometimes slow down GitHub Actions unpredictably
- **Step-by-step feedback**: Console output shows exactly what's happening at each stage
- **Singlish maintainer workflow**: Optimized for "one" person handling releases while others contribute code

This tool is **not recommended** for highly collaborative projects where multiple people need to trigger releases. For that, use GitHub Actions.

## Prerequisites

- **Python 3.10+**
- **uv** (Python package manager): https://docs.astral.sh/uv/
- **GitHub CLI** (`gh`): https://cli.github.com/ - used for creating GitHub releases
- **LaTeX distribution** (preferred with `latexmk` to handle citation/reference error, but we can use what env you want)
- **Existing Zenodo deposit**: The script creates new versions, not new deposits. You must manually create the first version on Zenodo.

## Installation

```bash
# Clone or copy this tool somewhere
git clone <repo-url> zenodo-publisher
cd zenodo-publisher

# Install with uv
uv sync

# Or install globally
uv tool install .
```


## Project Setup

### 1. Create `.zenodo.env` in your project root

```bash
# Required
MAIN_BRANCH=main
BASE_NAME=MyProject
LATEX_DIR=papers/latex
PDF_BASE_NAME=main

# Zenodo configuration
PUBLISHER_TYPE=zenodo
ZENODO_TOKEN=your_token_here
ZENODO_CONCEPT_DOI=10.5281/zenodo.XXXXXXX
ZENODO_API_URL=https://zenodo.org/api

# Archive options (comma-separated: pdf, project)
ARCHIVE_TYPES=pdf,project
PERSIST_TYPES=pdf
ARCHIVE_DIR=./releases
```

### 2. Create a Makefile in your LaTeX directory

The script calls `make deploy` in the directory specified by `LATEX_DIR`. Your Makefile must have a `deploy` target:

```makefile
.PHONY: deploy
deploy: cleanall all
```

See `Makefile.example` for a complete template.
We recommand doing a clean (even the pdf) on the deploy action to handle possible outdated version artifact. But if your compile time is long enough, once done on your project, you can skip the clean, which will use the already compiled version.

### 3. Configure LaTeX for reproducible PDFs

For MD5 checksum comparison to work correctly, your PDFs must be reproducible. Add these lines to your `.tex` file:

```latex
\pdfinfoomitdate=1
\pdfsuppressptexinfo=-1
\pdftrailerid{}
```

This is highly recommanded, not mandatory, but without theses the only reference point between the git repo and the zenodo repo will be the version tag name. With this option, we can also compare the content of the previous version to make sure files are different. In theory, the version tag is in sync with the git one, but since we can manually edit theses, a backup solution is always appreciated.

## Configuration Options

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MAIN_BRANCH` | No | `main` | Branch to check for releases |
| `BASE_NAME` | Yes | - | Base name for output files (e.g., `MyProject-v1.0.0.pdf`) |
| `LATEX_DIR` | Yes | - | Path to LaTeX directory (relative to project root) |
| `PDF_BASE_NAME` | No | `main` | Name of the main PDF file (without `.pdf`) |
| `PUBLISHER_TYPE` | Yes | - | Set to `zenodo` to enable publishing |
| `ZENODO_TOKEN` | Yes | - | Your Zenodo API token |
| `ZENODO_CONCEPT_DOI` | Yes | - | Concept DOI of your Zenodo deposit |
| `ZENODO_API_URL` | No | `https://zenodo.org/api` | Use `https://sandbox.zenodo.org/api` for testing |
| `ARCHIVE_TYPES` | No | `pdf` | What to archive: `pdf`, `project`, or `pdf,project` |
| `PERSIST_TYPES` | No | `pdf` | What to save to `ARCHIVE_DIR` (rest goes to temp) |
| `ARCHIVE_DIR` | No | - | Directory to save persistent archives |

## Usage

```bash
# From your project directory (where .zenodo.env is located)
zenodo-publisher

# Or run directly
uv run python /path/to/zenodo-publisher/release.py
```

Then use the script at the root of your project.

## How It Works

### 1. Build LaTeX
Runs `make deploy` in `LATEX_DIR`. The script **stops on any error**.

### 2. Git Checks
- Verifies you're on `MAIN_BRANCH`
- Checks branch is up-to-date with remote (no unpushed/unpulled commits)
- Checks no local modifications exist

### 3. Tag Validation
- If tag doesn't exist: proceeds
- If tag exists: verifies it points to the latest commit on the remote branch

### 4. GitHub Release
Creates a GitHub release using `gh release create`. This automatically creates and pushes the tag.

### 5. Zenodo Checks
- Verifies the version doesn't already exist on Zenodo
- Compares file checksums (MD5) to detect changes
- If version exists with identical files: skips upload

### 6. Archive & Upload
- Creates PDF archive (and optionally project ZIP)
- The project ZIP uses `git archive` (\(\approx \) same as GitHub's ZIP), so untracked local files are excluded
- Uploads files to Zenodo
- PDF is set as the default preview

## Limitations

### Test on Sandbox First
Always test with `ZENODO_API_URL=https://sandbox.zenodo.org/api` before using production. The script doesn't handle all edge cases.

### Draft Handling
The script **discards existing drafts** on the Zenodo identified deposit by the concept DOI. If you're collaborating on Zenodo through the web interface while using this script, drafts may be lost.

### API Limitations
- Uses the legacy Zenodo API
- Maximum file size: **100 MB** per file
- Metadata is copied from the previous version; only the version field is updated
- Each version gets a new DOI (no custom DOI per release)

### First Version Required
You must manually create the first version on Zenodo before using this script. It only creates new versions of existing deposits.

### PDF Storage Philosophy
This tool assumes you **don't store PDFs in git**. PDFs are generated on-the-fly before upload. If your PDFs are already in the repository, consider using Zenodo's native GitHub integration instead.

## Troubleshooting

### "Project not initialized for Zenodo publisher"
Create a `.zenodo.env` file in your project root.

### "LaTeX directory not found"
Check that `LATEX_DIR` points to a valid directory containing your Makefile.

### "Files are identical/different to version X on Zenodo"
Your PDF hasn't changed. This usually means:
- You forgot to add the reproducible PDF settings
- Build artifacts from a previous build affected the output
- Version tag is the same, so the check update halt here
- Version tag is different but files content is the same so proceeded with new version

### GitHub CLI errors
Make sure `gh` is installed and authenticated: `gh auth login`

### Edge cases

Application half vibe coded, not optimized, not really clean, tested multiple times in sandbox, but be sure to test it on your specific usage before using it in production (test it with test git repo, and test sandbox zenodo repo)
