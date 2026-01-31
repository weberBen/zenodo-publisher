# Release Tool

Python script to publish latex-based paper as new GitHub project release and on paper publication sites like Zenodo.

## Features

- ✅ Automated LaTeX document compilation
- ✅ Git repository checks (branch, up-to-date status)
- ✅ Tag validation (ensures tag doesn't exist or points to latest commit)
- ✅ GitHub release creation
- ✅ PDF renaming with version tag
- ✅ Zenodo publication with automatic versioning

## Quick Start

1. Copy `.env.example` to `.env` and configure:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` with your settings:
   - `MAIN_BRANCH`: Your main branch name (default: `main`)
   - `LATEX_DIR`: Path to your LaTeX directory
   - `BASE_NAME`: Base name for PDF files
   - (Optional) Zenodo credentials for automatic publication

3. Run the release tool:
   ```bash
   python release.py
   ```

## Configuration

See [ZENODO_SETUP.md](ZENODO_SETUP.md) for detailed Zenodo configuration instructions.

## Workflow

1. **Build**: Compiles LaTeX document using Makefile
2. **Verify**: Checks git status and tag validity
3. **Release**: Creates GitHub release with specified tag
4. **Rename**: Copies `main.pdf` to `{BASE_NAME}-{TAG_NAME}.pdf`
5. **Publish**: (Optional) Publishes new version to Zenodo

## Requirements

- Python 3.x
- Git
- GitHub CLI (`gh`)
- LaTeX distribution with `make`
- (Optional) Zenodo account for publication