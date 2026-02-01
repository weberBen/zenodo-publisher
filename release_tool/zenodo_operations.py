"""Zenodo operations for publishing releases using inveniordm-py."""

from datetime import datetime, timezone
from pathlib import Path

from inveniordm_py import InvenioAPI
from inveniordm_py.files.metadata import OutgoingStream


class ZenodoError(Exception):
    """Zenodo operation error."""
    pass


class ZenodoNoUpdateNeeded(Exception):
    """No update needed - version already exists."""
    pass


def get_zenodo_id_from_doi(doi: str) -> str:
    """Extract Zenodo record ID from a DOI string."""
    if not doi:
        return doi
    return doi.split("zenodo.")[-1]


class ZenodoPublisher:
    """Zenodo publisher using InvenioRDM API."""

    def __init__(
        self,
        access_token: str,
        zenodo_api_url: str,
        concept_doi: str,
        publication_date: str | None = None
    ):
        """
        Initialize the Zenodo publisher.

        Args:
            access_token: Zenodo access token
            zenodo_api_url: Zenodo API base URL
            concept_doi: Concept DOI of the record
            publication_date: Optional publication date (YYYY-MM-DD), defaults to today UTC
        """
        self.client = InvenioAPI(zenodo_api_url, access_token)
        self.concept_doi = concept_doi
        self.concept_id = get_zenodo_id_from_doi(concept_doi)
        self.publication_date = publication_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def get_latest_record(self):
        """
        Get the latest version record.

        Returns:
            Record resource with data loaded

        Raises:
            ZenodoError: If record cannot be found
        """
        try:
            return self.client.records(self.concept_id).versions.latest()
        except Exception as e:
            raise ZenodoError(f"Failed to find record with id {self.concept_id}: {e}")

    def check_update_needed(
        self,
        tag_name: str,
        archived_files: list[tuple[Path, str]]
    ) -> str:
        """
        Check if an update is needed by comparing version names and file checksums.

        Args:
            tag_name: New version name to publish
            archived_files: List of tuples (file_path, md5_checksum) to upload

        Returns:
            Record ID of the latest version if update is needed

        Raises:
            ZenodoNoUpdateNeeded: If version already exists or files are identical
        """
        print("  Checking if update is needed...")

        latest_record = self.get_latest_record()
        record_data = latest_record.data._data
        record_id = record_data["id"]
        current_version = record_data.get("metadata", {}).get("version", "")

        # Check version
        if current_version == tag_name:
            raise ZenodoNoUpdateNeeded(
                f"Version '{tag_name}' already exists on Zenodo"
            )

        # Compare MD5 checksums
        previous_version_files = record_data.get("files", {}).get("entries", [])
        previous_version_md5s = {
            f["checksum"].replace("md5:", "")
            for f in previous_version_files
            if f.get("checksum", "")
        }
        new_md5s = {md5 for _, md5 in archived_files}

        if previous_version_md5s == new_md5s:
            raise ZenodoNoUpdateNeeded(
                f"Files are identical to version '{current_version}' on Zenodo"
            )

        # Count differences
        new_files = new_md5s - previous_version_md5s
        removed_files = previous_version_md5s - new_md5s

        print(f"  ✓ New version '{tag_name}' (current: '{current_version}')")
        print(f"    {len(new_files)} new/modified file(s), {len(removed_files)} removed file(s)")

        return record_id

    def _upload_files(
        self,
        draft,
        archived_files: list[tuple[Path, str]],
        default_preview_file: str | None = None
    ) -> None:
        """
        Upload files to a draft record.

        Args:
            draft: Draft resource object
            archived_files: List of tuples (file_path, md5_checksum)
            default_preview_file: Filename to set as default preview (usually PDF)
        """
        # Sort files to put PDF first
        sorted_files = sorted(
            archived_files,
            key=lambda x: x[0].suffix.lower() != '.pdf'
        )

        # Register all files
        file_entries = [{"key": file_path.name} for file_path, _ in sorted_files]
        draft.files.create(file_entries)

        # Upload content and commit each file
        for file_path, _ in sorted_files:
            print(f"  Uploading {file_path.name}...")
            with open(file_path, "rb") as f:
                file_content = f.read()

            draft_file = draft.files(file_path.name)
            draft_file.set_contents(OutgoingStream(file_content))
            draft_file.commit()
            print(f"  ✓ {file_path.name} uploaded")

        # Set default preview
        if default_preview_file:
            draft.data["files"]["default_preview"] = default_preview_file
            draft.update()

    def _update_metadata(self, draft, version: str) -> None:
        """
        Update the metadata of a draft.

        Args:
            draft: Draft resource object
            version: Version string
        """
        draft.data["metadata"]["version"] = version
        draft.data["metadata"]["publication_date"] = self.publication_date
        draft.update()

    def publish_new_version(
        self,
        archived_files: list[tuple[Path, str]],
        tag_name: str,
        record_id: str
    ) -> str:
        """
        Publish a new version on Zenodo.

        Args:
            archived_files: List of tuples (file_path, md5_checksum) to upload
            tag_name: Tag name (used as version)
            record_id: Record ID of the latest version

        Returns:
            DOI of the published version

        Raises:
            ZenodoError: If publication fails
        """
        print(f"\n📤 Publishing new version to Zenodo...")
        print(f"  Concept DOI: {self.concept_doi}")
        print(f"  Version: {tag_name}")

        try:
            # Create new version draft
            print("  Creating new version...")
            record = self.client.records(record_id)
            draft = record.new_version()
            print(f"  ✓ New version draft created (ID: {draft.data['id']})")

            # Find PDF file for default preview
            pdf_file = next(
                (fp.name for fp, _ in archived_files if fp.suffix.lower() == '.pdf'),
                None
            )

            # Upload files
            print("  Uploading files...")
            self._upload_files(draft, archived_files, pdf_file)

            # Update metadata
            print(f"  Updating metadata (version: {tag_name})...")
            self._update_metadata(draft, tag_name)
            print("  ✓ Metadata updated")

            # Publish
            print("  Publishing...")
            published_record = draft.publish()
            doi = published_record.data["pids"]["doi"]["identifier"]
            record_html = published_record.data["links"]["self_html"]

            print(f"✓ Published to Zenodo!")
            print(f"  DOI: https://doi.org/{doi}")
            print(f"  URL: {record_html}")

            return doi

        except Exception as e:
            raise ZenodoError(f"Failed to publish new version: {e}")
