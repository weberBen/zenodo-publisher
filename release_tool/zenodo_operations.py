"""Zenodo operations for publishing releases using inveniordm-py."""

from datetime import datetime, timezone, timedelta
from pathlib import Path

from inveniordm_py import InvenioAPI
from inveniordm_py.files.metadata import FilesListMetadata, OutgoingStream
from requests.exceptions import HTTPError


class ZenodoError(Exception):
    """Zenodo operation error."""
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
        self._publication_date = publication_date
    
    
    def get_publication_date(self):
        return self._publication_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    def _get_last_record(self):
        try:
            concept_record = self.client.records(self.concept_id).versions.latest()
            return self.client.records(concept_record.data["id"]).get()
        except Exception as e:
            raise ZenodoError(f"Failed to find record with id {self.concept_id}: {e}")
    
    def _is_draft(self, record_id: str) -> bool:
        try:
            self.client.records(record_id).draft.get()
            return True
        except HTTPError as e:
            if e.response.status_code == 404:
                return False
            raise
    
    def _get_exsiting_draft_id(self):
        """
            Check if a draft already existed (not just created).
        """
        response = self.client.session.get(
            f"{self.client._base_url}/user/records",
            params={
                "q": 'conceptrecid:"432538"' 
            }
        )
        response.raise_for_status()
        response_data = response.json()
        
        hits = response_data["hits"]["hits"]
        if len(hits) == 0:
            raise ZenodoError(f"Cannot found deposit associated to record {self.concept_id}")
        
        record_data = hits[0]
        
        if record_data["status"] == "draft":
            return record_data["id"]
        
        return None

    def _discard_draft_version(self, record_id):
        self.client.records(record_id).draft.delete()
    
    def _create_new_draft_version(self, last_record):
        # API only allow one draft new version per repo, return the same draft
        # at each call before draft is published or discarded
        record = last_record.new_version()
        new_draft = self.client.records(record.data["id"]).draft.get()
        return new_draft

    def is_up_to_date(
        self,
        tag_name: str,
        archived_files: list[tuple[Path, str]]
    ) -> str:
        last_record = self._get_last_record()
        return self._is_up_to_date(tag_name, last_record, archived_files)
    
    def _is_up_to_date(
        self,
        tag_name: str,
        last_record,
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
        
        current_version = last_record.data["metadata"].get("version", None)

        # Check version
        if current_version == tag_name:
            return (True, f"Version '{tag_name}' already exists on Zenodo")

        # Compare MD5 checksums - use the proper API to get files
        files_metadata = last_record.files.get()
        previous_version_files = files_metadata.data["entries"]
        previous_version_md5s = {
            f["checksum"].replace("md5:", "")
            for f in previous_version_files
            if f.get("checksum", "")
        }
        new_md5s = {md5 for _, md5 in archived_files}

        if previous_version_md5s == new_md5s:
            return (True, f"Files are identical to version '{current_version}' on Zenodo")

        # Count differences
        new_files = new_md5s - previous_version_md5s
        removed_files = previous_version_md5s - new_md5s
        
        msg = f"  ✓ New version '{tag_name}' (current: '{current_version}')"
        msg += "\n"
        msg += f"    {len(new_files)} new/modified file(s), {len(removed_files)} removed file(s)"
        
        return (False, msg)

    def _upload_files(
        self,
        draft_record,
        archived_files: list[tuple[Path, str]],
        default_preview_file: str | None = None,
    ) -> None:
        """
        Upload files to the cached draft.

        Args:
            archived_files: List of tuples (file_path, md5_checksum)
            default_preview_file: Filename to set as default preview (usually PDF)
        """

        # Register all files
        file_entries = [{"key": file_path.name} for file_path, _ in archived_files]
        draft_record.files.create(FilesListMetadata(file_entries))

        # Upload content and commit each file
        for file_path, _ in archived_files:
            print(f"  Uploading {file_path.name}...")
            with open(file_path, "rb") as f:
                file_content = f.read()

            draft_file = draft_record.files(file_path.name)
            stream = OutgoingStream()
            stream._data = file_content
            draft_file.set_contents(stream)
            draft_file.commit()
            print(f"  ✓ {file_path.name} uploaded")

        # Set default preview
        if default_preview_file:
            draft_record.data["files"]["default_preview"] = default_preview_file
            draft_record.update()

    def _update_metadata(self, draft_record, publication_date, version: str) -> None:
        """
        Update the metadata of the cached draft.

        Args:
            version: Version string
        """
        draft_record.data["metadata"]["version"] = version
        draft_record.data["metadata"]["publication_date"] = publication_date
        draft_record.update()

    def publish_new_version(
        self,
        archived_files: list[tuple[Path, str]],
        tag_name: str,
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
        
        publication_date = self.get_publication_date()
        last_record = self._get_last_record()
        
        print(f"  Publication date: {publication_date}")
        print(f"")

        try:
            
            print("  Creating new draft version...")
            existing_draft_id = self._get_exsiting_draft_id()
            if existing_draft_id is not None:
                print(f"  ⚠️  Detecting existing draft version {existing_draft_id}, discarding...")
                self._discard_draft_version(existing_draft_id)
            else:
                print("  ✓ No existing draft detected")
            
            draft_record = self._create_new_draft_version(last_record)

            # verification
            if (
                (not self._is_draft(draft_record.data["id"]))
                or 
                (draft_record.data["id"] == last_record.data["id"])
            ):
                raise ZenodoError("Cannot create draft new version...")
            
            # Find PDF file for default preview
            pdf_filename = next(
                (fp.name for fp, _ in archived_files if fp.suffix.lower() == '.pdf'),
                None
            )
            
            # Upload files
            print("  Uploading files...")
            self._upload_files(
                draft_record,
                archived_files,
                default_preview_file=pdf_filename
            )

            # Update metadata
            print(f"  Updating metadata (version: {tag_name})...")
            self._update_metadata(draft_record, publication_date, tag_name)
            print("  ✓ Metadata updated")

            # Publish
            print("  Publishing...")
            published_record = draft_record.publish()
            doi = published_record.data["doi"]
            record_html = published_record.data["links"]["self_html"]

            print(f"✓ Published to Zenodo!")
            print(f"  DOI: https://doi.org/{doi}")
            print(f"  URL: {record_html}")

            return doi

        except Exception as e:
            raise ZenodoError(f"Failed to publish new version: {e}")
