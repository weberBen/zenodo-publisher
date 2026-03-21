"""Git operations and GitHub release management for the release tool."""

import json
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import tempfile
import shutil

from . import output
from .subprocess_utils import run as run_cmd
from .errors import ZPError


@dataclass
class ArchiveResult:
    """Result of a git archive operation."""
    file_path: Path
    archive_name: str
    format: str  # "zip", "tar", "tar.gz"


class GitError(ZPError):
    """Git operation error."""
    _prefix = "git"


class GitHubError(ZPError):
    """GitHub operation error."""
    _prefix = "github"


def run_git_command(args: list[str], cwd: Path) -> str:
    """
    Run a git command and return output.

    Args:
        args: Git command arguments
        cwd: Working directory

    Returns:
        Command output (stdout)

    Raises:
        GitError: If command fails
    """
    try:
        result = run_cmd(
            ["git"] + args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        # git dryrun write on stderr not stdout
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise GitError(f"Git command failed: {' '.join(args)}\n{e.stderr}", name="command_failed") from e


def get_current_branch(project_root: Path) -> str:
    """Get the current git branch name."""
    return run_git_command(["rev-parse", "--abbrev-ref", "HEAD"], project_root)


def check_on_main_branch(project_root: Path, main_branch: str) -> None:
    """
    Check if current branch is the main branch.

    Raises:
        GitError: If not on main branch
    """
    current = get_current_branch(project_root)
    if current != main_branch:
        raise GitError(
            f"Not on {main_branch} branch (currently on {current})\n"
            f"Please checkout {main_branch} first",
            name="not_on_main",
        )


def fetch_remote(project_root: Path) -> None:
    """Fetch updates from remote repository."""
    output.info("🔄 Fetching from remote...")
    run_git_command(["fetch"], project_root)


def is_up_to_date_with_remote(project_root: Path, main_branch: str) -> bool:
    """
    Check if local branch is up to date with remote.

    Returns:
        True if up to date, False otherwise
    """
    local = run_git_command(["rev-parse", main_branch], project_root)
    remote = run_git_command(["rev-parse", f"origin/{main_branch}"], project_root)
    return local == remote

def has_local_modifs(project_root: Path, main_branch: str) -> bool:
    """
    Check if working directory is clean (no local modifications).
    
    Uses 'git status --porcelain' which returns empty output if clean.
    
    Returns:
        True if no modifications, False if there are changes
    """
    result = run_git_command(["status", "--porcelain"], project_root)
    return result.strip() != ""

def has_unpushed_commits(project_root: Path, main_branch: str) -> bool:
    """
    Check if there are local commits not pushed to remote.
    Compares refs directly via git log, no text parsing.

    Returns:
        True if there are unpushed commits, False otherwise
    """
    result = run_git_command(
        ["log", f"origin/{main_branch}..HEAD", "--oneline"],
        project_root
    )
    return result.strip() != ""


def has_unpushed_tags(project_root: Path) -> bool:
    """
    Check if there are local tags not pushed to remote.
    Compares local refs vs remote refs directly, no text parsing.

    Returns:
        True if there are unpushed tags, False otherwise
    """
    local_tags = set(
        run_git_command(["tag", "-l"], project_root).splitlines()
    )
    
    # --refs allow to remove ^{} defference like 'v0.3.0^{}'
    remote_out = run_git_command(
        ["ls-remote", "--tags", "--refs", "origin"], project_root
    )
    remote_tags = {
        line.split("/")[-1]
        for line in remote_out.splitlines()
        if line and "^{}" not in line  # ignorer les lignes de déréférencement
    }
    
    return bool(local_tags - remote_tags)


def check_up_to_date(project_root: Path, main_branch: str) -> None:
    """
    Check if repository is up to date with remote.

    Raises:
        GitError: If repository is not up to date
    """
    fetch_remote(project_root)

    if has_local_modifs(project_root, main_branch):
        raise GitError(
            f"Local branch has local modifications/commits\n"
            f"Please commit or stash your changes first",
            name="local_modifications",
        )

    if has_unpushed_commits(project_root, main_branch):
        raise GitError(
            "Local commits are not pushed to remote\n"
            "Please push first: git push",
            name="unpushed_commits",
        )

    if not is_up_to_date_with_remote(project_root, main_branch):
        raise GitError(
            f"Local branch is not up to date with origin/{main_branch}\n"
            f"Please pull the latest changes first",
            name="not_up_to_date",
        )

    if has_unpushed_tags(project_root):
        raise GitError(
            "Local tags are not pushed to remote\n"
            "Please push tags first: git push --tags",
            name="unpushed_tags",
        )
        
    output.info_ok("Repository is up to date with origin/{branch}", branch=main_branch, name="git.repo_up_to_date")


def run_gh_command(args: list[str], cwd: Path) -> str:
    """
    Run a GitHub CLI command and return output.

    Args:
        args: gh command arguments
        cwd: Working directory

    Returns:
        Command output (stdout)

    Raises:
        GitHubError: If command fails
    """
    try:
        result = run_cmd(
            ["gh"] + args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise GitHubError(
            f"GitHub CLI command failed: {' '.join(args)}\n{e.stderr}",
            name="command_failed",
        ) from e
    except FileNotFoundError:
        raise GitHubError(
            "GitHub CLI (gh) not found. Please install it: https://cli.github.com/",
            name="cli_not_found",
        )


def get_latest_release(project_root: Path) -> Optional[dict]:
    """
    Get the latest GitHub release.

    Returns:
        Dictionary with release info (tagName, name, body) or None if no releases
    """
    try:
        # First, get the list of releases (without body field)
        result = run_gh_command(
            ["release", "list", "--limit", "1", "--json", "tagName,name"],
            project_root
        )
        if not result:
            return None

        releases = json.loads(result)
        if not releases:
            return None

        # Get the latest release tag
        latest_tag = releases[0]["tagName"]

        # Now get full details including body
        details = run_gh_command(
            ["release", "view", latest_tag, "--json", "tagName,name,body"],
            project_root
        )

        return json.loads(details)
    except (GitHubError, json.JSONDecodeError, KeyError, IndexError):
        return None


def get_commit_of_tag(project_root: Path, tag: str) -> str:
    """Get the commit hash that a tag points to.
    
    Works for both lightweight and annotated tags : the ^{commit} suffix
    explicitly dereferences annotated tag objects to their underlying commit.
    """
    return run_git_command(["rev-parse", f"{tag}^{{commit}}"], project_root)


def fetch_tag(project_root: Path, tag: str) -> None:
    """Fetch a single tag from origin into the local repo."""
    run_git_command(["fetch", "origin", "tag", tag], project_root)


def get_tag_info(project_root: Path, tag: str) -> str:
    """Get tag object SHA.

    For annotated tags, sha is the tag object hash and annotation is the message.
    For lightweight tags, sha equals the commit hash and annotation is empty.
    """
    # Tag object SHA (differs from commit SHA for annotated tags)
    return run_git_command(["rev-parse", tag], project_root)

def get_commit(project_root: Path, commit: str = "HEAD")  -> str:
    """Get the commit hash."""
    return run_git_command(["rev-parse", commit], project_root)
    
def get_latest_commit(project_root: Path) -> str:
    """Get the latest commit hash."""
    return get_commit(project_root, commit="HEAD")


def get_commit_info(project_root: Path, commit: str = "HEAD", tag_name=None) -> dict:
    """Get commit metadata, current branch, and remote origin URL.

    Args:
        project_root: Path to project root
        commit: Commit reference (default: HEAD)
    """
    # Commit info (single command)
    result = run_git_command(
        ["log", "-1", "--format=%H%n%ct%n%cn%n%ce%n%an%n%ae%n%s", commit], project_root
    )
    sha, timestamp, c_name, c_email, a_name, a_email, subject = result.split("\n", 6)

    branch = get_current_branch(project_root)
    origin_url = get_remote_url(project_root)

    result = {
        "ZP_COMMIT_DATE_EPOCH": timestamp,
        "ZP_COMMIT_SHA": sha,
        "ZP_COMMIT_SUBJECT": subject,
        "ZP_COMMIT_COMMITTER_NAME": c_name,
        "ZP_COMMIT_COMMITTER_EMAIL": c_email,
        "ZP_COMMIT_AUTHOR_NAME": a_name,
        "ZP_COMMIT_AUTHOR_EMAIL": a_email,
        "ZP_BRANCH": branch,
        "ZP_ORIGIN_URL": origin_url,
    }
    
    if tag_name:
        result["ZP_COMMIT_TAG"] = tag_name
        fetch_tag(project_root, tag_name)
        tag_sha = get_tag_info(project_root, tag_name)
        result["ZP_TAG_SHA"] = tag_sha
    
    return result

def get_last_commit_info(project_root: Path, tag_name=None):
    return get_commit_info(project_root, commit="HEAD", tag_name=tag_name)

def get_remote_latest_commit(project_root: Path, main_branch: str) -> str:
    """Get the latest commit hash from the remote main branch."""
    return run_git_command(["rev-parse", f"origin/{main_branch}"], project_root)


def tag_exists(project_root: Path, tag_name: str) -> bool:
    """
    Check if a tag exists (locally or remotely).

    Args:
        project_root: Path to project root
        tag_name: Name of the tag to check

    Returns:
        True if tag exists, False otherwise
    """
    try:
        # Check if tag exists locally
        run_git_command(["rev-parse", tag_name], project_root)
        return True
    except GitError:
        pass

    try:
        # Check if tag exists on remote using ls-remote
        result = run_git_command(
            ["ls-remote", "--tags", "origin", f"refs/tags/{tag_name}"],
            project_root
        )
        return bool(result.strip())
    except GitError:
        return False


def check_tag_validity(project_root: Path, tag_name: str, main_branch: str) -> None:
    """
    Verify that the tag either doesn't exist, or if it exists,
    points to the latest commit on the remote main branch.

    Args:
        project_root: Path to project root
        tag_name: Name of the tag to check
        main_branch: Name of the main branch

    Raises:
        GitError: If tag exists but doesn't point to the latest remote commit
    """
    if not tag_exists(project_root, tag_name):
        output.info_ok("Tag '{tag}' does not exist yet", tag=tag_name, name="git.tag_new")
        return

    # Tag exists, check if it points to the latest remote commit
    output.warn("Tag '{tag}' already exists, verifying it points to latest commit...", tag=tag_name, name="git.tag_exists")
    tag_commit = get_commit_of_tag(project_root, tag_name)
    remote_latest = get_remote_latest_commit(project_root, main_branch)

    if tag_commit == remote_latest:
        output.info_ok("Tag '{tag}' points to the latest remote commit", tag=tag_name, name="git.tag_valid")
        return

    raise GitError(
        f"Tag '{tag_name}' already exists but doesn't point to the latest remote commit\n"
        f"Tag points to: {tag_commit}\n"
        f"Latest remote commit (origin/{main_branch}): {remote_latest}\n"
        f"Please use a different tag name",
        name="tag_invalid",
    )


def is_latest_commit_released(project_root: Path) -> tuple[bool, Optional[dict]]:
    """
    Check if the latest commit has a GitHub release.

    Returns:
        Tuple of (is_released, release_info)
    """
    latest_release = get_latest_release(project_root)
    if not latest_release:
        return False, None

    release_tag = latest_release["tagName"]
    tag_commit = get_commit_of_tag(project_root, release_tag)
    latest_commit = get_latest_commit(project_root)

    if tag_commit == latest_commit:
        return True, latest_release

    return False, latest_release


def create_github_release(
    project_root: Path,
    tag_name: str,
    title: str,
    notes: str
) -> None:
    """
    Create a GitHub release.

    Args:
        project_root: Path to project root
        tag_name: Name of the tag for the release
        title: Release title
        notes: Release notes/description
    """
    output.info("Creating GitHub release '{tag}'...", tag=tag_name, name="github.creating_release")

    run_gh_command(
        ["release", "create", tag_name, "--title", title, "--notes", notes],
        project_root
    )

    output.info_ok("Release '{tag}' created and published", tag=tag_name, name="github.release_published")


def verify_release_on_latest_commit(project_root: Path, tag_name: str) -> None:
    """
    Verify that a release exists for the latest commit.

    Raises:
        GitHubError: If release doesn't exist or doesn't point to latest commit
    """
    latest_release = get_latest_release(project_root)

    if not latest_release:
        raise GitHubError("No releases found", name="no_releases")

    if latest_release["tagName"] != tag_name:
        raise GitHubError(
            f"Latest release tag '{latest_release['tagName']}' "
            f"doesn't match expected '{tag_name}'",
            name="tag_mismatch",
        )

    tag_commit = get_commit_of_tag(project_root, tag_name)
    latest_commit = get_latest_commit(project_root)

    if tag_commit != latest_commit:
        raise GitHubError(
            f"Release '{tag_name}' does not point to the latest commit\n"
            f"Release commit: {tag_commit}\n"
            f"Latest commit: {latest_commit}",
            name="release_commit_mismatch",
        )

def get_git_ref(project_root, tag_name):    
    return get_commit_of_tag(project_root, tag_name)
            
def archive_zip_project(
    project_root: Path,
    tag_name: str,
    project_name: str,
    output_dir: Path,
) -> ArchiveResult:
    """
    Create a zip archive of the project at the given tag.

    Always creates the archive in a temporary directory. The caller is
    responsible for moving the file to a persistent location if needed.

    Args:
        project_root: Path to project root
        tag_name: Git tag to archive (used for naming)
        project_name: Project name for the archive
        output_dir: Directory for the output zip file

    Returns:
        ArchiveResult with file path and metadata

    Raises:
        GitError: If archive creation fails
    """
    output_file = output_dir / f"{project_name}.zip"

    git_ref = get_git_ref(project_root, tag_name)
    run_git_command(
        ["archive", "--format=zip", f"--prefix={project_name}/", "-o", str(output_file), git_ref],
        project_root
    )

    output.info_ok("Created archive: {output_file}", output_file=str(output_file), name="archive.created")
    return ArchiveResult(file_path=output_file, archive_name=project_name, format="zip")


def archive_zip_remote_project(
    repo_url: str,
    tag_name: str,
    project_name: str,
    output_dir: Path,
) -> ArchiveResult:
    """
    Create a zip archive from a remote git repository at the given tag.

    Performs a shallow fetch of the tag into a temporary repository,
    runs git archive, then cleans up. No .zenodo.env required.

    Args:
        repo_url: Git remote URL (HTTPS or SSH)
        tag_name: Git tag to archive (used for naming)
        project_name: Full formatted project name for the archive
        output_dir: Directory for the output zip file
    Returns:
        ArchiveResult with file path and metadata

    Raises:
        GitError: If any git operation fails
    """

    output_file = output_dir / f"{project_name}.zip"

    tmp_dir = Path(tempfile.mkdtemp())
    tmp_repo = tmp_dir / "tmp_repo"

    try:
        refspec = f"refs/tags/{tag_name}:refs/tags/{tag_name}"

        run_git_command(["init", str(tmp_repo)], cwd=tmp_dir)
        run_git_command(["remote", "add", "origin", repo_url], cwd=tmp_repo)
        run_git_command(["fetch", "--depth=1", "origin", refspec], cwd=tmp_repo)

        git_ref = get_git_ref(tmp_repo, tag_name)
        run_git_command(
            ["archive", "--format=zip", f"--prefix={project_name}/", "-o", str(output_file), git_ref],
            cwd=tmp_repo,
        )

        output.info_ok("Created archive: {output_file}", output_file=str(output_file), name="archive.created")
        return ArchiveResult(file_path=output_file, archive_name=project_name, format="zip")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def get_remote_url(project_root: Path) -> str:
    """Get the remote 'origin' URL of a local git repository.

    Raises:
        GitError: If the remote URL cannot be retrieved
    """
    return run_git_command(["remote", "get-url", "origin"], project_root)


# ---------------------------------------------------------------------------
# Post-archive utilities (extract, tree hash, reproducible tar)
# ---------------------------------------------------------------------------

def extract_zip(zip_path: Path, dest_dir: Path) -> Path:
    """Extract a ZIP archive into dest_dir.

    If the ZIP contains a single root directory (e.g. ProjectName-tag/),
    returns that subdirectory. Otherwise returns dest_dir.
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)

    entries = list(dest_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return dest_dir


def compute_tree_hash(content_dir: Path, object_format: str = "sha1") -> str:
    """Compute a git tree hash by initing git directly in content_dir.

    Initialises a temporary git repo in *content_dir* with the requested
    object format (sha1 or sha256), stages everything with ``git add --all``,
    and returns the output of ``git write-tree``.

    The ``.git`` directory is removed in the ``finally`` block so the caller
    gets the directory back in its original state.

    Raises:
        GitError: If content_dir already contains a .git directory.
    """
    git_dir = content_dir / ".git"
    if git_dir.exists():
        raise GitError(f"content_dir already contains .git: {content_dir}", name="tree_hash_conflict")
    
    run_git_command(["init", f"--object-format={object_format}", "."], cwd=content_dir)
    run_git_command(["config", "user.email", "noop@noop.local"], cwd=content_dir)
    run_git_command(["config", "user.name", "noop"], cwd=content_dir)
    run_git_command(["add", "--all"], cwd=content_dir)
    return run_git_command(["write-tree"], cwd=content_dir)


def pack_tar(
    content_dir: Path,
    output_path: Path,
    compress_gz: bool = False,
    tar_args: list[str] | None = None,
    gzip_args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> None:
    """Create a TAR (or TAR.GZ) from *content_dir* using ``tar``.

    *content_dir* is expected to be e.g. ``/tmp/xxx/ProjectName-tag/``.
    The directory name itself becomes the archive prefix.

    *tar_args* and *gzip_args* are the final merged argument lists
    (defaults + user overrides), built by config_schema transforms.

    *env* is the subprocess environment (e.g. for reproducibility vars).
    """
    parent = content_dir.parent
    dirname = content_dir.name

    # 1. Always produce the .tar first
    tar_path = output_path if not compress_gz else output_path.with_suffix("")
    tar_cmd = ["tar"] + (tar_args or []) + ["-cf", str(tar_path), "-C", str(parent), dirname]
    run_cmd(tar_cmd, check=True, env=env)

    # 2. Compress with gzip if tar.gz requested
    #    gzip replaces file.tar with file.tar.gz automatically
    if compress_gz:
        run_cmd(["gzip"] + (gzip_args or []) + [str(tar_path)], check=True, env=env)



def get_release_asset_digest(
    project_root: Path,
    tag_name: str,
    asset_name: str,
) -> str | None:
    """Return the SHA256 digest of a release asset, or None if it doesn't exist.

    Uses the REST API (gh api) because gh release view does not expose the digest field.
    """
    try:
        result = run_gh_command(
            ["api", "repos/{owner}/{repo}/releases/tags/" + tag_name,
             "--jq", f'.assets[] | select(.name == "{asset_name}") | .digest'],
            project_root,
        )
        if result:
            return result  # e.g. "sha256:29ca0d..."
    except GitHubError:
        pass
    return None



def upload_release_asset(
    project_root: Path,
    tag_name: str,
    file_path: Path,
    clobber: bool = False,
) -> None:
    """Upload a file as a GitHub release asset."""
    args = ["release", "upload", tag_name, str(file_path)]
    if clobber:
        args.append("--clobber")
    run_gh_command(args, project_root)