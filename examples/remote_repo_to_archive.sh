#!/usr/bin/env bash
# git-archive-download.sh
# Download a git archive (ZIP) from a remote repository at a given tag or commit
set -euo pipefail

# ── Help ─────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage:
  $(basename "$0") --repo <url> --tag <tag>    [--prefix <name>] [--output <file>]
  $(basename "$0") --repo <url> --commit <sha> [--prefix <name>] [--output <file>]

Description:
  Fetch a git archive (ZIP) from a remote repository at a specific tag or commit,
  without cloning the full history. Prints MD5 and SHA-256 checksums.

Options:
  --repo    <url>   Remote repository URL (SSH or HTTPS)         [required]
  --tag     <ref>   Tag name to archive (e.g. v1.0.0)
  --commit  <sha>   Commit SHA to archive (full or short)
  --prefix  <name>  Root folder name inside the ZIP              [default: <repo>-<ref>]
  --output  <file>  Output ZIP path                              [default: ./<prefix>.zip]
  --help            Show this help message

Examples:
  # Archive a tag
  ./$(basename "$0") --repo git@github.com:user/myrepo.git --tag v1.2.3

  # Archive a specific commit
  ./$(basename "$0") --repo https://github.com/user/myrepo.git --commit a1b2c3d

  # Custom prefix and output path
  ./$(basename "$0") --repo git@github.com:user/myrepo.git --tag v1.2.3 \\
      --prefix MyProject-v1.2.3 --output /tmp/myproject.zip
EOF
}

# ── Argument parsing ──────────────────────────────────────────
REPO=""
REF=""
REF_TYPE=""   # "tag" or "commit"
PREFIX=""
OUTPUT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)   REPO="$2";   shift 2 ;;
        --tag)    REF="$2";    REF_TYPE="tag";    shift 2 ;;
        --commit) REF="$2";    REF_TYPE="commit"; shift 2 ;;
        --prefix) PREFIX="$2"; shift 2 ;;
        --output) OUTPUT="$2"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Error: unknown option '$1'" >&2; usage >&2; exit 1 ;;
    esac
done

# ── TMP WORKDIR ────────────────────────────────────────────────
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT
TMP_REPO="$TMPDIR/repo"

# ── Validation ────────────────────────────────────────────────
if [[ -z "$REPO" ]]; then
    echo "Error: --repo is required" >&2; usage >&2; exit 1
fi
if [[ -z "$REF" ]]; then
    echo "Error: either --tag or --commit is required" >&2; usage >&2; exit 1
fi

# Derive a short repo name from the URL (e.g. "myrepo" from "git@github.com:user/myrepo.git")
REPO_NAME="$(basename "$REPO" .git)"

# Default prefix and output
[[ -z "$PREFIX" ]] && PREFIX="MyProject"
[[ -z "$OUTPUT" ]] && OUTPUT="./${PREFIX}.zip"

OUTPUT="$(realpath -m "$OUTPUT")"  # resolve without requiring the file to exist

# ── Fetch refspec ─────────────────────────────────────────────
# For a tag  → fetch the specific ref
# For a commit → fetch all (shallow) since commits have no named refspec
if [[ "$REF_TYPE" == "tag" ]]; then
    REFSPEC="refs/tags/${REF}:refs/tags/${REF}"
else
    REFSPEC=""   # plain shallow fetch; we'll use the commit SHA directly
fi

# ── Clone Repo ─────────────────────────────
echo "→ Initialising temporary repository..." >&2
git init --quiet "$TMP_REPO"
git -C "$TMP_REPO" remote add origin "$REPO"

if [[ "$REF_TYPE" == "tag" ]]; then
    echo "→ Fetching tag '$REF'..." >&2
    git -C "$TMP_REPO" fetch --quiet --depth=1 origin "$REFSPEC"
    ARCHIVE_REF="$REF"
else
    echo "→ Fetching commit '$REF'..." >&2
    # Some servers support direct commit fetch; fall back to full shallow fetch if needed
    if ! git -C "$TMP_REPO" fetch --quiet --depth=1 origin "$REF" 2>/dev/null; then
        echo "→ Direct commit fetch not supported, falling back to shallow clone..." >&2
        git -C "$TMP_REPO" fetch --quiet --depth=1 origin
    fi
    ARCHIVE_REF="$REF"
fi

# ── Tag info ─────────────────────────────────────────────────
if [[ "$REF_TYPE" == "tag" ]]; then
    # Resolve the tag object SHA
    TAG_SHA="$(git -C "$TMP_REPO" rev-parse "refs/tags/${REF}")"
    # Resolve the commit SHA the tag points to (^{} dereferences annotated tags)
    COMMIT_SHA="$(git -C "$TMP_REPO" rev-parse "refs/tags/${REF}^{commit}")"

    # If the tag SHA differs from the commit SHA, it is an annotated tag object
    if [[ "$TAG_SHA" != "$COMMIT_SHA" ]]; then
        TAG_KIND="annotated"
    else
        TAG_KIND="lightweight"
    fi

    echo "" >&2
    if [[ "$TAG_KIND" == "annotated" ]]; then
        echo "Tag      : $REF (annotated)"
        echo "Tag SHA  : $TAG_SHA"
    else
        echo "Tag      : $REF (lightweight)"
    fi
    echo "Commit   : $COMMIT_SHA"
fi


# ── Create archive ────────────────────────────────────────────
echo "→ Creating archive (prefix: ${PREFIX}/)..." >&2
git -C "$TMP_REPO" archive \
    --format=zip \
    --prefix="${PREFIX}/" \
    -o "$OUTPUT" \
    "$ARCHIVE_REF"

# ── Checksums ─────────────────────────────────────────────────
echo "" >&2
echo "Archive : $OUTPUT"
echo "MD5     : $(md5sum    "$OUTPUT" | awk '{print $1}')"
echo "SHA-256 : $(sha256sum "$OUTPUT" | awk '{print $1}')"
