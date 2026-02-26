"""Git operations and GitHub release management for the release tool."""

import subprocess
import json
import random
from pathlib import Path
from typing import Optional
import tempfile

from . import output

class GitError(Exception):
    """Git operation error."""
    pass


class GitHubError(Exception):
    """GitHub operation error."""
    pass


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
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise GitError(f"Git command failed: {' '.join(args)}\n{e.stderr}") from e


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
            f"Please checkout {main_branch} first"
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
    

def check_up_to_date(project_root: Path, main_branch: str) -> None:
    """
    Check if repository is up to date with remote.

    Raises:
        GitError: If repository is not up to date
    """
    fetch_remote(project_root)

    if not is_up_to_date_with_remote(project_root, main_branch):
        raise GitError(
            f"Local branch is not up to date with origin/{main_branch}\n"
            f"Please pull/push the latest changes first"
        )
    if has_local_modifs(project_root, main_branch):
        raise GitError(
            f"Local branch has local modififs/commit\n"
            f"Please pull/push the latest changes first"
        )
        
    output.info_ok(f"Repository is up to date with origin/{main_branch}")


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
        result = subprocess.run(
            ["gh"] + args,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise GitHubError(
            f"GitHub CLI command failed: {' '.join(args)}\n{e.stderr}"
        ) from e
    except FileNotFoundError:
        raise GitHubError(
            "GitHub CLI (gh) not found. Please install it: https://cli.github.com/"
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
    """Get the commit hash that a tag points to."""
    return run_git_command(["rev-list", "-n", "1", tag], project_root)


def get_commit(project_root: Path, commit: str = "HEAD")  -> str:
    """Get the commit hash."""
    return run_git_command(["rev-parse", commit], project_root)
    
def get_latest_commit(project_root: Path) -> str:
    """Get the latest commit hash."""
    return get_commit(project_root, commit="HEAD")


def get_commit_info(project_root: Path, commit: str = "HEAD") -> dict:
    """Get timestamp (epoch), SHA, committer name and email of a commit.

    Args:
        project_root: Path to project root
        commit: Commit reference (default: HEAD)
    """
    # Commit info (single command)
    result = run_git_command(
        ["log", "-1", "--format=%H%n%ct%n%cn%n%ce%n%an%n%ae%n%s", commit], project_root
    )
    sha, timestamp, c_name, c_email, a_name, a_email, subject = result.split("\n", 6)

    return {
        "ZP_COMMIT_DATE_EPOCH": timestamp,
        "ZP_COMMIT_SHA": sha,
        "ZP_COMMIT_SUBJECT": subject,
        "ZP_COMMIT_COMMITTER_NAME": c_name,
        "ZP_COMMIT_COMMITTER_EMAIL": c_email,
        "ZP_COMMIT_AUTHOR_NAME": a_name,
        "ZP_COMMIT_AUTHOR_EMAIL": a_email,
    }

def get_last_commit_info(project_root: Path):
    return get_commit_info(project_root, commit="HEAD")

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
        output.info_ok(f"Tag '{tag_name}' does not exist yet")
        return

    # Tag exists, check if it points to the latest remote commit
    output.warn(f"Tag '{tag_name}' already exists, verifying it points to latest commit...")
    tag_commit = get_commit_of_tag(project_root, tag_name)
    remote_latest = get_remote_latest_commit(project_root, main_branch)

    if tag_commit == remote_latest:
        output.info_ok(f"Tag '{tag_name}' points to the latest remote commit")
        return

    raise GitError(
        f"Tag '{tag_name}' already exists but doesn't point to the latest remote commit\n"
        f"Tag points to: {tag_commit}\n"
        f"Latest remote commit (origin/{main_branch}): {remote_latest}\n"
        f"Please use a different tag name or delete the existing tag"
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
    output.info(f"🚀 Creating GitHub release '{tag_name}'...")

    run_gh_command(
        ["release", "create", tag_name, "--title", title, "--notes", notes],
        project_root
    )

    output.info_ok(f"Release '{tag_name}' created and published")


def verify_release_on_latest_commit(project_root: Path, tag_name: str) -> None:
    """
    Verify that a release exists for the latest commit.

    Raises:
        GitHubError: If release doesn't exist or doesn't point to latest commit
    """
    latest_release = get_latest_release(project_root)

    if not latest_release:
        raise GitHubError("No releases found")

    if latest_release["tagName"] != tag_name:
        raise GitHubError(
            f"Latest release tag '{latest_release['tagName']}' "
            f"doesn't match expected '{tag_name}'"
        )

    tag_commit = get_commit_of_tag(project_root, tag_name)
    latest_commit = get_latest_commit(project_root)

    if tag_commit != latest_commit:
        raise GitHubError(
            f"Release '{tag_name}' does not point to the latest commit\n"
            f"Release commit: {tag_commit}\n"
            f"Latest commit: {latest_commit}"
        )

    output.info_ok(f"Release '{tag_name}' points to the latest commit")

def archive_project(
    project_root: Path,
    tag_name: str,
    project_name: str,
    archive_dir: Optional[Path] = None,
    persist: bool = False
) -> Path:
    """
    Create a zip archive of the project at the given tag.

    Args:
        project_root: Path to project root
        tag_name: Git tag to archive
        project_name: Project name for the archive
        archive_dir: Directory to save the archive (required if persist=True)
        persist: If True, save to archive_dir; if False, create temp file

    Returns:
        Path to the zip file

    Raises:
        GitError: If archive creation fails
    """
    archive_name = f"{project_name}-{tag_name}"

    if persist and archive_dir:
        output_file = archive_dir / f"{archive_name}.zip"
    else:
        output_file = Path(tempfile.gettempdir()) / f"{archive_name}.zip"

    run_git_command(
        ["archive", "--format=zip", f"--prefix={archive_name}/", "-o", str(output_file), tag_name],
        project_root
    )

    output.info_ok(f"Created archive: {output_file}")
    return output_file, archive_name, "zip"


def add_zenodo_asset_to_release(
    project_root: Path,
    tag_name: str,
    doi: str,
    record_url: str,
    archived_files: list,
    debug: bool = False
) -> Path:
    """
    Create a zenodo_publication_info.json and attach it as asset to a GitHub release.

    Args:
        project_root: Path to project root
        tag_name: Tag name of the release
        doi: Zenodo DOI
        record_url: Zenodo record URL
        archived_files: List of tuples (file_path, md5, is_preview, filename, persist_file)
        debug: If True, anonymize the DOI number with a random value
    """
    doi_url = f"https://doi.org/{doi}"

    if debug:
        rand_id = random.randint(100000, 999999)
        doi_parts = doi.rsplit(".", 1)
        if len(doi_parts) == 2:
            doi_url = f"https://doi.org/{doi_parts[0]}.{rand_id}"
        record_url = f"https://zenodo.org/records/{rand_id}"

    info = {
        "doi": doi_url,
        "record_url": record_url,
        "files": [
            {"key": e["file_path"].name, "md5": e["md5"]}
            for e in archived_files
        ],
    }

    info_path = Path(tempfile.gettempdir()) / "zenodo_publication_info.json"
    with open(info_path, "w") as f:
        json.dump(info, f, indent=2)
        f.write("\n")

    output.detail(f"Adding zenodo publication info to release '{tag_name}'...")
    run_gh_command(
        ["release", "upload", tag_name, str(info_path), "--clobber"],
        project_root
    )
    output.detail_ok("Zenodo publication info added to release")
    return info_path