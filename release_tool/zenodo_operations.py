"""Zenodo operations for publishing releases using inveniordm-py."""

import json
from datetime import datetime, timezone
from pathlib import Path

from . import output

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

    def __init__(self, config):
        self.client = InvenioAPI(config.zenodo_api_url, config.zenodo_token)
        self.concept_doi = config.zenodo_concept_doi
        self.concept_id = get_zenodo_id_from_doi(config.zenodo_concept_doi)
        self._publication_date = config.publication_date
        self.config = config
        self._setup_http_logging()

    def _setup_http_logging(self):
        def _on_response(response, *args, **kwargs):
            output.data("http_request", {
                "method": response.request.method,
                "url": response.request.url,
                "status": response.status_code,
            })
        self.client.session.hooks["response"].append(_on_response)

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
        """Check if a draft already existed (not just created)."""
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
        # API only allows one draft new version per record. Calling new_version()
        # returns the same draft until it is published or discarded.
        record = last_record.new_version()
        new_draft = self.client.records(record.data["id"]).draft.get()
        return new_draft

    def _format_record_info(self, record):
        return {
            "doi": record.data["doi"],
            "record_url": record.data["links"]["self_html"],
        }

    def is_up_to_date(self, tag_name: str, archived_files: list) -> tuple[bool, str, dict | None]:
        """Returns (up_to_date, msg, record_info or None)."""
        last_record = self._get_last_record()
        up_to_date, msg = self._is_up_to_date(tag_name, last_record, archived_files)

        record_info = None
        if up_to_date:
            record_info = self._format_record_info(last_record)

        return up_to_date, msg, record_info

    def _is_up_to_date(self, tag_name: str, last_record, archived_files: list) -> tuple[bool, str]:
        """Check if an update is needed by comparing version names and file checksums."""
        output.detail("Checking if update is needed...")

        current_version = last_record.data["metadata"].get("version", None)
        versions_equal = (current_version == tag_name)

        files_metadata = last_record.files.get()
        previous_version_files = files_metadata.data["entries"]

        sig_extensions = {".asc", ".sig"}
        previous_version_md5s = {
            (f["checksum"].replace("md5:", ""),
             any(f.get("key", "").endswith(ext) for ext in sig_extensions))
            for f in previous_version_files
            if f.get("checksum", "")
        }
        new_md5s = {(af.hashes["md5"]["value"], af.is_signature) for af in archived_files}

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
            return (False, f"Version names are identical ('{tag_name}'). ⚠️ But files contents are different (Files {files_msg})")

        return (False, f"Files and version are different.\nVersion {versions_msg}\nFiles {files_msg}")

    def _upload_files(self, draft_record, archived_files: list) -> None:
        """Upload ArchivedFile entries to the draft."""
        file_entries = [{"key": af.file_path.name} for af in archived_files]
        draft_record.files.create(FilesListMetadata(file_entries))

        default_preview_file = None
        for af in archived_files:
            if af.is_preview:
                default_preview_file = af.file_path.name

            output.detail("Uploading {filename}...", filename=af.file_path.name, name="zenodo.uploading")
            with open(af.file_path, "rb") as f:
                file_content = f.read()

            draft_file = draft_record.files(af.file_path.name)
            stream = OutgoingStream()
            stream._data = file_content
            draft_file.set_contents(stream)
            draft_file.commit()
            output.detail_ok("{filename} uploaded", filename=af.file_path.name, name="zenodo.uploaded")

        if default_preview_file:
            draft_record.data["files"]["default_preview"] = default_preview_file
            draft_record.update()

    def _load_metadata_overrides(self, identifiers: list | None = None) -> dict | None:
        """Load metadata overrides from .zenodo.json.

        Raises ZenodoError if 'version' is present or identifiers collide.
        """
        zenodo_json = self.config.project_root / ".zenodo.json"
        if not zenodo_json.exists():
            output.warn("No .zenodo.json found, skipping metadata update")
            return None

        try:
            with open(zenodo_json) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            raise ZenodoError(f"Failed to read .zenodo.json: {e}")

        overrides = data.get("metadata", data)

        if "version" in overrides:
            raise ZenodoError(
                ".zenodo.json contains 'version' — this is set by the pipeline (git tag). "
                "Remove it from .zenodo.json to continue."
            )

        if "publication_date" in overrides:
            output.warn(
                ".zenodo.json overrides publication_date to '{override_date}' "
                "(config value '{config_date}' will be ignored)",
                override_date=overrides['publication_date'],
                config_date=self._publication_date or 'today UTC',
                name="zenodo.date_override",
            )

        # Check for identifier collisions
        if "identifiers" in overrides and identifiers:
            id_prefixes = set()
            for af in identifiers:
                prefix = af.identifier_value.split(":")[0] if ":" in af.identifier_value else None
                if prefix:
                    id_prefixes.add(prefix)

            collisions = [
                i for i in overrides["identifiers"]
                if any(i.get("identifier", "").startswith(f"{p}:") for p in id_prefixes)
            ]
            if collisions:
                collision_values = [c["identifier"] for c in collisions]
                raise ZenodoError(
                    f".zenodo.json identifiers conflict with pipeline identifiers: "
                    f"{collision_values}. Remove them from .zenodo.json to continue."
                )

        return overrides if overrides else None

    def _update_metadata(self, draft_record, publication_date, version: str,
                         identifiers: list | None = None,
                         metadata_overrides: dict | None = None) -> None:
        """Update the metadata of the draft.

        Args:
            version: Version string (git tag)
            identifiers: List of ArchivedFile with identifier_value set
            metadata_overrides: Dict from .zenodo.json
        """
        if metadata_overrides:
            output.detail("Applying metadata overrides: {keys}", keys=str(list(metadata_overrides.keys())), name="zenodo.metadata_overrides")
            if "publication_date" in metadata_overrides:
                publication_date = metadata_overrides.pop("publication_date")
            draft_record.data["metadata"].update(metadata_overrides)

        draft_record.data["metadata"]["version"] = version
        draft_record.data["metadata"]["publication_date"] = publication_date

        if identifiers:
            existing = draft_record.data["metadata"].get("identifiers", [])
            # Remove existing identifiers that would collide
            for af in identifiers:
                prefix = af.identifier_value.split(":")[0] if ":" in af.identifier_value else None
                if prefix:
                    existing = [i for i in existing
                                if not i.get("identifier", "").startswith(f"{prefix}:")]
                existing.append({"scheme": "other", "identifier": af.identifier_value})
            draft_record.data["metadata"]["identifiers"] = existing

        draft_record.update()

    def publish_new_version(
        self,
        archived_files: list,
        tag_name: str,
        identifiers: list | None = None,
    ) -> dict:
        """Publish a new version on Zenodo.

        Args:
            archived_files: List of ArchivedFile entries to upload
            tag_name: Tag name (used as version)
            identifiers: List of ArchivedFile with identifier_value set

        Returns:
            Record info dict with 'doi' and 'record_url'

        Raises:
            ZenodoError: If publication fails
        """
        output.info("📤 Publishing new version to Zenodo...")
        output.detail("Concept DOI: {doi}", doi=self.concept_doi, name="zenodo.concept_doi")
        output.detail("Version: {version}", version=tag_name, name="zenodo.version")

        publication_date = self.get_publication_date()
        last_record = self._get_last_record()

        output.detail("Publication date: {date}", date=publication_date, name="zenodo.pub_date")

        try:
            output.detail("Creating new draft version...")
            existing_draft_id = self._get_exsiting_draft_id()
            if existing_draft_id is not None:
                output.warn("Detecting existing draft version {draft_id}, discarding...", draft_id=existing_draft_id, name="zenodo.draft_discard")
                self._discard_draft_version(existing_draft_id)
            else:
                output.detail_ok("No existing draft detected")

            draft_record = self._create_new_draft_version(last_record)

            if (
                (not self._is_draft(draft_record.data["id"]))
                or
                (draft_record.data["id"] == last_record.data["id"])
            ):
                raise ZenodoError("Cannot create draft new version...")

            output.detail("Uploading files...")
            self._upload_files(draft_record, archived_files)

            output.detail("Updating metadata (version: {version})...", version=tag_name, name="zenodo.metadata_update")
            metadata_overrides = self._load_metadata_overrides(identifiers=identifiers)
            self._update_metadata(draft_record, publication_date, tag_name,
                                  identifiers=identifiers,
                                  metadata_overrides=metadata_overrides)
            output.detail_ok("Metadata updated")

            output.detail("Publishing...")
            published_record = draft_record.publish()
            record_info = self._format_record_info(published_record)

            output.info_ok("Published to Zenodo!")
            output.detail("DOI: https://doi.org/{doi}", doi=record_info['doi'], name="zenodo.published_doi")
            output.detail("URL: {url}", url=record_info['record_url'], name="zenodo.published_url")

            return record_info

        except Exception as e:
            if self.config.debug:
                raise e
            raise ZenodoError(f"Failed to publish new version: {e}")
