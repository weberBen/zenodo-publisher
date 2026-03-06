"""Zenodo REST API client for E2E test verification.

Independent from release_tool. Uses requests directly.
"""

import requests
from pathlib import Path


class ZenodoClient:
    """Lightweight REST client for verifying Zenodo state after publication."""

    def __init__(self, api_url: str, token: str):
        self.api_url = api_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    # --- Records ---

    def get_record(self, record_id: str) -> dict:
        r = self.session.get(f"{self.api_url}/records/{record_id}")
        r.raise_for_status()
        return r.json()

    def get_record_metadata(self, record_id: str) -> dict:
        record = self.get_record(record_id)
        return record.get("metadata", {})

    def get_latest_version(self, concept_id: str) -> dict:
        r = self.session.get(f"{self.api_url}/records/{concept_id}/versions/latest")
        r.raise_for_status()
        return r.json()

    def _get_versions(
        self,
        concept_doi: str,
        max_items: int | None = 10,
        fields: list[str] | None = None,
        all_fields: bool = False,
        page_size: int = 10,
        sort: str = "newest"
    ) -> list[dict]:
        """Get all published versions for a concept DOI.

        Paginates automatically. Returns records ordered by creation date
        (newest first).

        Args:
            concept_doi: Concept DOI or numeric concept ID.
            max: Maximum number of records to return (None = all).
            fields: List of top-level fields to keep per record.
                    If None and all_fields is False, returns the full record.
            all_fields: Explicitly return all fields (same as fields=None,
                        provided for readability).
        """
        concept_id = concept_doi.split(".")[-1] if "." in concept_doi else concept_doi
        page = 1
        all_hits: list[dict] = []
        
        # page_size = min(v for v in [max_items, 100, page_size] if v is not None)
        
        while True:
            r = self.session.get(
                f"{self.api_url}/records",
                params={
                    "q": f'parent.id:"{concept_id}"',
                    "all_versions": True,
                    "size": page_size,
                    "page": page,
                    "sort": sort,
                },
            )
            r.raise_for_status()
            data = r.json()
            hits = data.get("hits", {}).get("hits", [])
            if not hits:
                break
            all_hits.extend(hits)
            if max_items is not None and len(all_hits) >= max_items:
                break
            
            total = data.get("hits", {}).get("total", 0)
            if len(all_hits) >= total:
                break
            
            page += 1
        
        if max_items is not None:
            all_hits = all_hits[:max_items]

        if fields is not None and not all_fields:
            all_hits = [{k: h[k] for k in fields if k in h} for h in all_hits]

        return all_hits

    def get_all_versions(self,
        concept_doi: str,
        max: int | None = 10,
        fields: list[str] | None = None,
        all_fields: bool = False,
        page_size: int = 10,
    ):
        return self._get_versions(concept_doi,
                             max_items=max, fields=fields,
                             all_fields=all_fields,
                             page_size=page_size)
    
    def get_user_draft(
        self,
        concept_id: str | None = None,
    ) -> list[dict]:
        """Get user's draft records, optionally filtered by concept ID.

        Uses the /api/user/records endpoint which returns drafts visible
        to the authenticated user.

        Args:
            concept_id: Concept DOI or numeric ID to filter on (None = all drafts).
            max: Maximum number of records to return (None = all).
            fields: List of top-level fields to keep per record.
            all_fields: Explicitly return all fields.
        """
        cid = None
        if concept_id:
            cid = concept_id.split(".")[-1] if "." in concept_id else concept_id

        page_size = 10
        hit = None

        q = f'parent.id:"{cid}"' if cid else ""
        r = self.session.get(
            f"{self.api_url}/user/records",
            params={
                "q": q,
                "page": 1,
                "size": page_size,
                "shared_with_me": "false",
                "sort": "newest"
            },
        )
            
        r.raise_for_status()
        data = r.json()
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            return None
        hit = hits[0]
        
        if "status" not in hit:
            return None
        if hit["status"] != "draft":
            return None
        
        return hit

    def get_last_modified_record(self, concept_id: str) -> dict | None:
        """Get the most recently modified record (published or draft) for a concept.

        Fetches all user records for the concept and sorts client-side by
        `updated` (API sort=updated-desc is unreliable on some instances).

        Args:
            concept_id: Concept DOI or numeric concept ID.

        Returns:
            The record dict with the latest `updated` timestamp, or None.
        """
        hits = self._get_versions(concept_id, max_items=1, sort="updated-desc")
        if not hits:
            return None
        return hits[0]

    # --- Files ---

    def list_files(self, record_id: str) -> list[dict]:
        """List files for a record. Returns [{key, checksum, size}, ...]."""
        r = self.session.get(f"{self.api_url}/records/{record_id}/files")
        r.raise_for_status()
        return r.json().get("entries", [])

    def download_file(self, record_id: str, filename: str, dest_dir: Path) -> Path:
        """Download a single file from a record."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        r = self.session.get(
            f"{self.api_url}/records/{record_id}/files/{filename}/content",
            stream=True,
        )
        r.raise_for_status()
        dest = dest_dir / filename
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return dest

    def download_all_files(self, record_id: str, dest_dir: Path) -> list[Path]:
        """Download all files from a record."""
        files = self.list_files(record_id)
        paths = []
        for f in files:
            paths.append(self.download_file(record_id, f["key"], dest_dir))
        return paths

    # --- Draft management ---

    def delete_draft(self, record_id: str):
        """Delete a draft version (best-effort, ignores errors)."""
        r = self.session.delete(f"{self.api_url}/records/{record_id}/draft")
        r.raise_for_status()
