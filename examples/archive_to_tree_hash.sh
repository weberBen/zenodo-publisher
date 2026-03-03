#!/usr/bin/env bash
# git-tree-hash.sh
# Usage: ./git-tree-hash.sh <archive> [--sha256]
# Compute the git tree hash of a ZIP or TAR archive without polluting the project
set -euo pipefail

# ── Help ─────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage:
  $(basename "$0") <archive.zip> [--sha256]

Description:
  Compute the git tree hash of a ZIP or TAR archive without polluting the project.
  The hash is produced via 'git write-tree' (no commit, no timestamp).

Supported formats:
  .zip  .tar  .tar.gz  .tgz  .tar.bz2  .tar.xz

Options:
  --sha256    Use SHA-256 instead of SHA-1 (default)
  --help      Show this help message

Examples:
  # SHA-1 hash (default)
  ./$(basename "$0") my-project.zip
  ./$(basename "$0") my-project.tar.gz

  # SHA-256 hash
  ./$(basename "$0") my-project.zip --sha256
  ./$(basename "$0") my-project.tar.xz --sha256

  # From a different directory
  ./$(basename "$0") /path/to/archive.tar.gz --sha256
EOF
}

# ── Arguments ────────────────────────────────────────────────
ZIP="${1:-}"
HASH_FORMAT="sha1"   # default: SHA-1

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
fi

if [[ "${2:-}" == "--sha256" ]]; then
    HASH_FORMAT="sha256"
fi

if [[ -z "$ZIP" ]]; then
    usage >&2
    exit 1
fi

if [[ ! -f "$ZIP" ]]; then
    echo "Error: file '$ZIP' not found" >&2
    exit 1
fi

# Resolve absolute path before changing directory
ZIP_ABS="$(realpath "$ZIP")"

# ── Temporary directory (auto-cleaned on exit) ───────────────
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT
EXTRACT_DIR="$TMPDIR/content"
GIT_DIR="$TMPDIR/repo"
mkdir -p "$EXTRACT_DIR" "$GIT_DIR"

# ── Extraction ────────────────────────────────────────────────
echo "→ Extracting $(basename "$ZIP")..." >&2
case "$ZIP_ABS" in
    *.zip)                unzip -q "$ZIP_ABS" -d "$EXTRACT_DIR" ;;
    *.tar)                tar -xf   "$ZIP_ABS" -C "$EXTRACT_DIR" ;;
    *.tar.gz|*.tgz)       tar -xzf  "$ZIP_ABS" -C "$EXTRACT_DIR" ;;
    *.tar.bz2)            tar -xjf  "$ZIP_ABS" -C "$EXTRACT_DIR" ;;
    *.tar.xz)             tar -xJf  "$ZIP_ABS" -C "$EXTRACT_DIR" ;;
    *)
        echo "Error: unsupported archive format '$(basename "$ZIP")'" >&2
        echo "Supported: .zip  .tar  .tar.gz  .tgz  .tar.bz2  .tar.xz" >&2
        exit 1 ;;
esac

# If the ZIP contains a single root folder, descend into it
# (common behaviour for GitHub/GitLab archives)
CONTENTS=("$EXTRACT_DIR"/*)
if [[ ${#CONTENTS[@]} -eq 1 && -d "${CONTENTS[0]}" ]]; then
    EXTRACT_DIR="${CONTENTS[0]}"
    echo "→ Root folder detected: $(basename "$EXTRACT_DIR")" >&2
fi

# ── Git init ──────────────────────────────────────────────────
echo "→ Initialising git repository ($HASH_FORMAT)..." >&2
git init --quiet \
    --object-format="$HASH_FORMAT" \
    "$GIT_DIR"

# Copy extracted files into the git repository
cp -r "$EXTRACT_DIR/." "$GIT_DIR/"
cd "$GIT_DIR"

# ── Git add + write-tree ──────────────────────────────────────
echo "→ Staging files..." >&2
# Configure git identity (required for git add)
git config user.email "noop@noop.local"
git config user.name  "noop"

# Exclude any .git folder that may have been present in the archive
git add --all

# write-tree produces the tree hash WITHOUT creating a commit
# → no timestamp, no author metadata
TREE_HASH="$(git write-tree)"

# ── Result ────────────────────────────────────────────────────
echo ""
echo "Git tree hash ($HASH_FORMAT):"
echo "$TREE_HASH"
