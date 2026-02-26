"""Zenodo operations for publishing releases using inveniordm-py."""

from datetime import datetime, timezone, timedelta
from pathlib import Path

from . import output
from .config import IDENTIFIER_HASH_TYPE

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
        config,
    ):
        """
        Initialize the Zenodo publisher.

        Args:
            access_token: Zenodo access token
            zenodo_api_url: Zenodo API base URL
            concept_doi: Concept DOI of the record
            publication_date: Optional publication date (YYYY-MM-DD), defaults to today UTC
        """
        self.client = InvenioAPI(config.zenodo_api_url, config.zenodo_token)
        self.concept_doi = config.zenodo_concept_doi
        self.concept_id = get_zenodo_id_from_doi(config.zenodo_concept_doi)
        self._publication_date = config.publication_date
        self.config = config
        
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
                "q": f'conceptrecid:"{self.concept_id}"' 
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
        archived_files: list
    ) -> str:
        last_record = self._get_last_record()
        return self._is_up_to_date(tag_name, last_record, archived_files)
    
    def _is_up_to_date(
        self,
        tag_name: str,
        last_record,
        archived_files: list
    ) -> str:
        """
        Check if an update is needed by comparing version names and file checksums.

        Args:
            tag_name: New version name to publish
            archived_files: List of tuples to upload

        Returns:
            Record ID of the latest version if update is needed

        Raises:
            ZenodoNoUpdateNeeded: If version already exists or files are identical
        """
        output.detail("Checking if update is needed...")
        
        # Check version
        current_version = last_record.data["metadata"].get("version", None)
        versions_equal = (current_version == tag_name)
        
        # print(f" \tGit version: '{tag_name}' | Zenodo version: '{current_version}'")

        # Compare MD5 checksums - use the proper API to get files
        files_metadata = last_record.files.get()
        previous_version_files = files_metadata.data["entries"]
        
        # Build sets of (md5, is_signature) tuples for both sides
        sig_extensions = {".asc", ".sig"}
        previous_version_md5s = {
            (f["checksum"].replace("md5:", ""),
             any(f.get("key", "").endswith(ext) for ext in sig_extensions))
            for f in previous_version_files
            if f.get("checksum", "")
        }
        new_md5s = {(e["md5"], e["is_signature"]) for e in archived_files}

        if self.config.gpg_sign:
            # Exclude signature files from comparison: GPG signatures contain
            # a timestamp, so their MD5 changes on every run even when the
            # signed content is identical.
            previous_version_md5s = {md5 for md5, is_sig in previous_version_md5s if not is_sig}
            new_md5s = {md5 for md5, is_sig in new_md5s if not is_sig}


        files_equal = (previous_version_md5s == new_md5s)
        files_changes = new_md5s - previous_version_md5s

        versions_msg = f"Git: '{tag_name}' | Zenodo: '{current_version}"
        sig_note = " *signature files ignored" if self.config.gpg_sign else ""
        files_msg = f"Changes : +/- {len(files_changes)}{sig_note}"

        if files_equal and versions_equal:
            return (True, f"Files and version are identical to previous version '{current_version}' on Zenodo")
        if files_equal and not versions_equal:
            return (True, f"Files are identical to previous version '{current_version}' on Zenodo\n⚠️ But version names are different ({versions_msg})")
        if not files_equal and versions_equal:
            return (False, f"Version names are identifial ('{tag_name}'). ⚠️ But files contents are different (Files {files_msg})")
        
        return (False, f"Files and version are different.\nVersion {versions_msg}\nFiles {files_msg}")

    def _upload_files(
        self,
        draft_record,
        archived_files: list,
    ) -> None:
        """
        Upload files to the cached draft.

        Args:
            archived_files: List of tuples
            default_preview_file: Filename to set as default preview (usually PDF)
        """

        # Register all files
        file_entries = [{"key": e["file_path"].name} for e in archived_files]
        draft_record.files.create(FilesListMetadata(file_entries))

        default_preview_file = None
        # Upload content and commit each file
        for entry in archived_files:
            if entry["is_preview"]:
                default_preview_file = entry["file_path"].name

            output.detail(f"Uploading {entry['file_path'].name}...")
            with open(entry["file_path"], "rb") as f:
                file_content = f.read()

            draft_file = draft_record.files(entry["file_path"].name)
            stream = OutgoingStream()
            stream._data = file_content
            draft_file.set_contents(stream)
            draft_file.commit()
            output.detail_ok(f"{entry['file_path'].name} uploaded")

        # Set default preview
        if default_preview_file:
            draft_record.data["files"]["default_preview"] = default_preview_file
            draft_record.update()

    def _update_metadata(self, draft_record, publication_date, version: str, identifier_hash: str | None = None) -> None:
        """
        Update the metadata of the cached draft.

        Args:
            version: Version string
            identifier_hash: Optional SHA256 hash to add as alternate identifier
        """
        draft_record.data["metadata"]["version"] = version
        draft_record.data["metadata"]["publication_date"] = publication_date

        if identifier_hash:
            identifiers = draft_record.data["metadata"].get("identifiers", [])
            # Remove any existing sha256 identifier from previous version
            identifiers = [i for i in identifiers if not i.get("identifier", "").startswith(f"{IDENTIFIER_HASH_TYPE}:")]
            identifiers.append({"scheme": "other", "identifier": f"{IDENTIFIER_HASH_TYPE}:{identifier_hash}"})
            draft_record.data["metadata"]["identifiers"] = identifiers
        
        draft_record.update()

    def publish_new_version(
        self,
        archived_files: list,
        tag_name: str,
        identifier_hash: str | None = None,
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
        output.info("📤 Publishing new version to Zenodo...")
        output.detail(f"Concept DOI: {self.concept_doi}")
        output.detail(f"Version: {tag_name}")

        publication_date = self.get_publication_date()
        last_record = self._get_last_record()

        output.detail(f"Publication date: {publication_date}")

        try:
            
            output.detail("Creating new draft version...")
            existing_draft_id = self._get_exsiting_draft_id()
            if existing_draft_id is not None:
                output.warn(f"Detecting existing draft version {existing_draft_id}, discarding...")
                self._discard_draft_version(existing_draft_id)
            else:
                output.detail_ok("No existing draft detected")
            
            draft_record = self._create_new_draft_version(last_record)

            # verification
            if (
                (not self._is_draft(draft_record.data["id"]))
                or 
                (draft_record.data["id"] == last_record.data["id"])
            ):
                raise ZenodoError("Cannot create draft new version...")
            
            # Upload files
            output.detail("Uploading files...")
            self._upload_files(
                draft_record,
                archived_files
            )

            # Update metadata
            output.detail(f"Updating metadata (version: {tag_name})...")
            self._update_metadata(draft_record, publication_date, tag_name, identifier_hash=identifier_hash)
            output.detail_ok("Metadata updated")

            # Publish
            output.detail("Publishing...")
            published_record = draft_record.publish()
            doi = published_record.data["doi"]
            record_html = published_record.data["links"]["self_html"]

            output.info_ok("Published to Zenodo!")
            output.detail(f"DOI: https://doi.org/{doi}")
            output.detail(f"URL: {record_html}")

            return doi, record_html

        except Exception as e:
            if self.config.debug:
                raise e
            raise ZenodoError(f"Failed to publish new version: {e}")
