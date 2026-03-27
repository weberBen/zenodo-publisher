"""GitHub CLI (gh) wrappers for E2E tests.

All functions call `gh` via subprocess. Independent from release_tool.
"""

import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class GithubClient:

    def __init__(self, repo_dir: Path | str):
        self.repo_dir = Path(repo_dir)
        if not self.repo_dir.exists() or not (self.repo_dir / ".git").exists():
            raise ValueError(f"Invalid git repo path: {self.repo_dir}")
        self.repo_url = self._get_repo_url()

    def _run(self, *args, check=True) -> subprocess.CompletedProcess:
        cmd = ["gh"] + list(args)
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

    def _get_repo_url(self) -> str:
        r = self._run("repo", "view", "--json", "url")
        return json.loads(r.stdout)["url"]

    def get_release_url(self, tag: str) -> str:
        return f"{self.repo_url}/releases/tag/{tag}"

    # --- Releases ---

    def list_releases(self, limit: int | None = None) -> list[dict]:
        args = ["release", "list", "--json",
                "tagName,name,isDraft,isPrerelease,createdAt,publishedAt"]
        if limit is not None:
            args += ["--limit", str(limit)]
        r = self._run(*args)
        return json.loads(r.stdout)

    def get_latest_release(self) -> dict:
        """Get the latest published release (non-draft, non-prerelease) via API."""
        r = self._run("api", "repos/{owner}/{repo}/releases/latest")
        return json.loads(r.stdout)

    def get_release_info(self, tag: str) -> dict:
        """Get full release info via API (includes target_commitish, assets, etc.)."""
        r = self._run("api", f"repos/{{owner}}/{{repo}}/releases/tags/{tag}")
        return json.loads(r.stdout)

    def list_draft_releases(self) -> list[dict]:
        """List only draft releases via REST API.

        gh release list does not include drafts, so we use the API directly.
        """
        r = self._run("api", "repos/{owner}/{repo}/releases", "--paginate")
        all_releases = json.loads(r.stdout)
        return [rel for rel in all_releases if rel.get("draft")]

    def create_release(self, tag: str, title: str = "",
                       body: str = "", files: list[Path] | None = None):
        args = ["release", "create", tag, "--title", title or tag, "--notes", body]
        for f in (files or []):
            args.append(str(f))
        self._run(*args)

    def delete_release(self, tag: str, cleanup_tag: bool = True):
        args = ["release", "delete", tag, "--yes"]
        if cleanup_tag:
            args.append("--cleanup-tag")
        self._run(*args, check=False)

    def edit_release(self, tag: str, title: str | None = None,
                     body: str | None = None, new_tag: str | None = None):
        args = ["release", "edit", tag]
        if title is not None:
            args += ["--title", title]
        if body is not None:
            args += ["--notes", body]
        if new_tag is not None:
            args += ["--tag", new_tag]
        self._run(*args)

    # --- Assets ---

    def list_release_assets(self, tag: str) -> list[dict]:
        """List assets via REST API (includes digest field)."""
        r = self._run("api", f"repos/{{owner}}/{{repo}}/releases/tags/{tag}",
                       "--jq", ".assets")
        return json.loads(r.stdout)

    def get_release_asset(self, tag: str, filename: str) -> dict | None:
        assets = self.list_release_assets(tag)
        return next((a for a in assets if a.get("name") == filename), None)

    def upload_asset(self, tag: str, file_path: Path, clobber: bool = False):
        args = ["release", "upload", tag, str(file_path)]
        if clobber:
            args.append("--clobber")
        self._run(*args)

    def delete_asset(self, tag: str, filename: str):
        """Delete a single asset from a release (via API)."""
        assets = self.list_release_assets(tag)
        for asset in assets:
            if asset.get("name") == filename:
                self._run("api", "-X", "DELETE",
                          f"repos/{{owner}}/{{repo}}/releases/assets/{asset['id']}")
                return
        raise ValueError(f"Asset '{filename}' not found in release {tag}")

    def download_asset(self, tag: str, filename: str, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        self._run("release", "download", tag,
                  "--pattern", filename, "--dir", str(dest_dir))
        return dest_dir / filename

    def download_all_assets(self, tag: str, dest_dir: Path) -> list[Path]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        self._run("release", "download", tag, "--dir", str(dest_dir))
        return list(dest_dir.iterdir())

    # --- Tags ---

    def list_tags(self) -> list[dict]:
        """List all tags via API. Returns [{name, commit_sha}, ...]."""
        r = self._run("api", "repos/{owner}/{repo}/tags", "--paginate")
        tags = json.loads(r.stdout)
        return [{"name": t["name"], "commit_sha": t["commit"]["sha"]} for t in tags]

    def get_tag_info(self, tag: str) -> dict:
        """Get tag info via API: type (lightweight/annotated), commit, and tag object if annotated.

        Returns dict with keys:
        - name: tag name
        - type: "lightweight" or "annotated"
        - commit_sha: the commit the tag ultimately points to
        - tag_sha: sha of the tag object (annotated only)
        - message: tag message (annotated only)
        - tagger: tagger info (annotated only)
        """
        r = self._run("api", f"repos/{{owner}}/{{repo}}/git/refs/tags/{tag}")
        ref = json.loads(r.stdout)
        obj = ref["object"]

        if obj["type"] == "commit":
            return {"name": tag, "type": "lightweight", "commit_sha": obj["sha"]}

        # annotated tag: resolve to get commit + metadata
        r2 = self._run("api", f"repos/{{owner}}/{{repo}}/git/tags/{obj['sha']}")
        tag_obj = json.loads(r2.stdout)
        return {
            "name": tag,
            "type": "annotated",
            "commit_sha": tag_obj["object"]["sha"],
            "tag_sha": obj["sha"],
            "message": tag_obj.get("message", ""),
            "tagger": tag_obj.get("tagger"),
        }

    def get_tags_for_commit(self, commit_sha: str) -> list[str]:
        """Get all tags pointing to a given commit (via API)."""
        all_tags = self.list_tags()
        return [t["name"] for t in all_tags if t["commit_sha"] == commit_sha]

    def has_release(self, tag: str) -> bool:
        r = self._run("api", f"repos/{{owner}}/{{repo}}/releases/tags/{tag}",
                       check=False)
        return r.returncode == 0

    def delete_tag(self, tag: str, dangerous_delete=False):
        """Delete a tag on the remote via API. Raises if the tag has a release.

        Note GitHub behavior: deleting a tag that a release depends on turns
        the release into a draft with a URL `untagged-<HASH>`. The `gh`
        commands (edit, delete, etc.) keep working even with the associated
        tag deleted, because the release retains the `tag_name` as internal
        metadata, even without the git tag.
        Deleting a tag that has an associated release is therefore forbidden.
        """
        if not dangerous_delete and self.has_release(tag):
            raise ValueError(f"Cannot delete tag '{tag}': it has an associated release. Delete the release first.")
        self._run("api", "-X", "DELETE",
                  f"repos/{{owner}}/{{repo}}/git/refs/tags/{tag}")
