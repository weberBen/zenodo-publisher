"""Git wrappers for E2E tests.

All functions call git via subprocess. Independent from release_tool.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

TEMPLATE_BRANCH = "template"
class GitClient:

    def __init__(self, repo_dir: Path | str):
        self.repo_dir = Path(repo_dir)
        if not self.repo_dir.exists() or not (self.repo_dir / ".git").exists():
            raise ValueError(f"Invalid git repo path: {self.repo_dir}")

    def _run(self, *args, check=True) -> subprocess.CompletedProcess:
        cmd = ["git"] + list(args)
        logger.debug("$ %s", " ".join(cmd))
        r = subprocess.run(
            cmd, cwd=str(self.repo_dir), check=False,
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            msg = f"Command failed: {' '.join(cmd)}"
            if r.stderr.strip():
                msg += f"\nstderr: {r.stderr.strip()}"
            if r.stdout.strip():
                msg += f"\nstdout: {r.stdout.strip()}"
            if check:
                raise RuntimeError(msg)
            logger.debug(msg)
        return r

    # --- Init / Config ---

    @staticmethod
    def init(repo_dir: Path | str, initial_branch: str = "main") -> "GitClient":
        """Init a new git repo with user config and return a GitClient."""
        repo_dir = Path(repo_dir)
        repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", initial_branch],
                       cwd=str(repo_dir), check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.email", "test@test.local"],
                       cwd=str(repo_dir), check=True, capture_output=True, text=True)
        subprocess.run(["git", "config", "user.name", "Test User"],
                       cwd=str(repo_dir), check=True, capture_output=True, text=True)
        return GitClient(repo_dir)

    def config_get(self, key: str) -> str:
        r = self._run("config", "--get", key)
        return r.stdout.strip()

    # --- Stage / Commit / Push ---

    def add(self, *paths: str):
        self._run("add", *paths)

    def add_all(self):
        self._run("add", ".")

    def commit(self, msg: str = "update"):
        self._run("commit", "-m", msg)

    def add_and_commit(self, msg: str = "update"):
        self.add_all()
        if self.is_clean():
            return
        self.commit(msg)

    def push(self, remote: str = "origin", branch: str | None = None):
        args = ["push", remote]
        if branch:
            args.append(branch)
        self._run(*args)

    def pull(self, remote: str = "origin"):
        self._run("pull", remote)

    # --- Tags ---

    def tag_create(self, tag: str, annotated: bool = False, msg: str = ""):
        if annotated:
            self._run("tag", "-a", tag, "-m", msg or f"Release {tag}")
        else:
            self._run("tag", tag)

    def tag_delete(self, tag: str, remote: bool = False):
        self._run("tag", "-d", tag)
        if remote:
            self._run("push", "origin", f":refs/tags/{tag}")

    def list_tags(self) -> list[str]:
        r = self._run("tag", "--list")
        return [t.strip() for t in r.stdout.strip().split("\n") if t.strip()]

    def tag_date(self, tag: str) -> str:
        """Return the creation date of a tag (ISO format).

        Uses creatordate which works for both annotated and lightweight tags.
        """
        r = self._run("for-each-ref", "--format=%(creatordate:iso)", f"refs/tags/{tag}")
        return r.stdout.strip()

    def latest_remote_tag(self, pattern: str, remote: str = "origin") -> str | None:
        """Fetch remote tags and return the most recent one matching pattern.

        Fetches all remote tags (--force to overwrite stale local copies),
        then sorts by creatordate descending and returns the first match.
        Returns None if no matching tag is found.
        """
        self._run("fetch", remote, "--tags", "--force")
        r = self._run("tag", "--list", pattern, "--sort=-creatordate")
        tags = [t.strip() for t in r.stdout.strip().split("\n") if t.strip()]
        return tags[0] if tags else None

    # --- Branch ---

    def branch_current(self) -> str:
        r = self._run("rev-parse", "--abbrev-ref", "HEAD")
        return r.stdout.strip()

    def branch_checkout(self, branch: str, create: bool = False):
        if create:
            self._run("checkout", "-b", branch)
        else:
            self._run("checkout", branch)

    # --- Status ---

    def is_clean(self) -> bool:
        r = self._run("status", "--porcelain")
        return r.stdout.strip() == ""

    def is_up_to_date(self, branch: str = "main") -> bool:
        if not self.is_clean():
            return False
        
        self._run("fetch", "origin")
        local = self._run("rev-parse", branch).stdout.strip()
        remote = self._run("rev-parse", f"origin/{branch}")
        if remote.returncode != 0:
            return True  # no remote tracking
        return local == remote.stdout.strip()

    def remote_url(self, remote: str = "origin") -> str:
        r = self._run("remote", "get-url", remote)
        return r.stdout.strip()

    def rev_parse(self, ref: str) -> str:
        r = self._run("rev-parse", ref)
        return r.stdout.strip()

    def diff_names(self, ref: str) -> list[str]:
        r = self._run("diff", ref, "--name-only")
        return [f.strip() for f in r.stdout.strip().split("\n") if f.strip()]

    # --- File helpers ---

    def add_file(self, path: str, content: str):
        """Write a file in the repo (creating parent dirs if needed)."""
        filepath = self.repo_dir / path
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)

    def reset(self, branch: str | None = None, remote: str = "origin"):
        if not branch:
            branch = self.branch_current()
        
        # Fetch latest refs from remote
        self._run("fetch", remote)
        # remove all local tags
        tags = self.list_tags()
        for tag in tags:
            self._run("tag", "-d", tag)
        # Hard reset current branch to match remote (discard local changes)
        self._run("reset", "--hard", f"{remote}/{branch}")
        # Remove all untracked (not ignored) files
        self._run("clean", "-fd")
        # Drop all stashed changes
        self._run("stash", "clear")    
        
    def reset_repo(self, branch: str, template_sha: str, remote: str = "origin"):
        """Reset a branch to match the template at a specific commit.

        1. Force-cleans the working tree (without depending on remote)
        2. Checks out the target branch
        3. Resets to remote state
        4. Replaces the entire working tree + index with the content
           from template_sha, ready to be committed

        The branch history is preserved — only the file content changes.
        """
        # Clean local state without depending on remote tracking
        self._run("reset", "--hard", "HEAD")
        self._run("clean", "-fd")

        # Switch to the target branch
        self._run("checkout", "-f", branch)

        # Delete all other local branches except target branch
        r = self._run("branch", "--list")
        for b in r.stdout.splitlines():
            b = b.strip().lstrip("* ")
            if b and b != branch:
                self._run("branch", "-D", b)

        # Reset target branch to remote state (also deletes all local tags)
        self.reset(branch)
        
        # Remove all tracked files from index and working tree
        self._run("rm", "-rf", ".")
        # Restore all files from the template commit (stays on current branch)
        self._run("checkout", template_sha, "--", ".")
