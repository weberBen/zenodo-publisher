"""Zenodo operations for publishing releases using inveniordm-py."""

from functools import cached_property
from datetime import datetime, timezone, timedelta
from pathlib import Path

from inveniordm_py import InvenioAPI
from inveniordm_py.files.metadata import OutgoingStream
from requests.exceptions import HTTPError


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


class CachedProperty:
    """Classe wrapper pour stocker la clé de cache"""
    def __init__(self, key, func):
        self.key = key
        self.func = func
        self._cache_key = key  # Pour add_reset_methods
    
    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        if self.key not in obj.__dict__:
            obj.__dict__[self.key] = self.func(obj)
        return obj.__dict__[self.key]
    
    def __set__(self, obj, value):
        raise AttributeError(f"Cannot update '{self.key}' manually")


def auto_cached_property(key):
    """Décorateur qui crée property + reset automatiquement avec clé personnalisée"""
    def decorator(func):
        prop = CachedProperty(key, func)
        prop._method_name = func.__name__  # Stocke le nom de la méthode
        return prop
    return decorator


def add_reset_methods(cls):
    """Décorateur de classe qui ajoute les méthodes reset_xxx() et les alias"""
    for attr_name in list(dir(cls)):  # list() pour éviter les modifications pendant l'itération
        attr = getattr(cls, attr_name, None)
        if isinstance(attr, CachedProperty):
            key = attr._cache_key
            method_name = attr._method_name
            
            # Crée un alias si la clé est différente du nom de méthode
            if key != method_name:
                setattr(cls, key, attr)
            
            # Crée la fonction reset
            reset_name = f'reset_{key}'
            def make_reset(k):
                def reset(self):
                    self.__dict__.pop(k, None)
                return reset
            
            setattr(cls, reset_name, make_reset(key))
    
    return cls

@add_reset_methods
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
        
    def _reset(self):
        self.reset_last_record()
        self.reset_deposit()
    
    @auto_cached_property("last_record")
    def _get_last_record(self):
        try:
            return self.client.records(self.concept_id).versions.latest()
        except Exception as e:
            raise ZenodoError(f"Failed to find record with id {self.concept_id}: {e}")
        
    @auto_cached_property("deposit")
    def _get_deposit(self):
        return self.client.deposit.depositions(self.last_record.data["id"])
    
    def _has_draft_version(self, draft_version_record):
        created = draft_version_record.get("created", datetime.now())
        modified = draft_version_record.get("modified", datetime.now())
        return (modified - created) > timedelta(seconds=10)

    def _discard_draft_version(self, draft_version_record):
         # URL : f"{zenodo_api_url}/deposit/depositions/{record_id}/actions/discard"
        self.deposit.discard(draft_version_record.data["id"])
    
    def _create_new_draft_version(self):
        # API only allow one draft new version per repo, return the same draft
        # at each call before draft is published or discarded
        return self.last_record.new_version()

    @auto_cached_property("new_draft_version")
    def _get_new_draft_version(self):
        return self._create_new_draft_version()

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

        record_data = self.last_record.data._data
        record_id = record_data["id"]
        current_version = record_data.get("metadata", {}).get("version", None)

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
        self.new_draft_version.files.create(file_entries)

        # Upload content and commit each file
        for file_path, _ in file_entries:
            print(f"  Uploading {file_path.name}...")
            with open(file_path, "rb") as f:
                file_content = f.read()

            draft_file = self.new_draft_version.files(file_path.name)
            draft_file.set_contents(OutgoingStream(file_content))
            draft_file.commit()
            print(f"  ✓ {file_path.name} uploaded")

        # Set default preview
        if default_preview_file:
            self.new_draft_version.data["files"]["default_preview"] = default_preview_file
            self.new_draft_version.update()

    def _update_metadata(self, version: str) -> None:
        """
        Update the metadata of the cached draft.

        Args:
            version: Version string
        """
        self.new_draft_version.data["metadata"]["version"] = version
        self.new_draft_version.data["metadata"]["publication_date"] = self.get_publication_date()
        self.new_draft_version.update()

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

        try:
            
            print("  Getting last record version...")
            self._get_last_record
            self._get_deposit
            print("  Creating new draft version...")
            record = self._create_new_draft_version()
            if self._has_draft_version(record):
                print(f"Detecting draft version {record.data['id']}")

                self._discard_draft_version(record)
                record = self._create_new_draft_version()
            
            self._get_new_draft_version
            
            # Find PDF file for default preview
            pdf_filename = next(
                (fp.name for fp, _ in archived_files if fp.suffix.lower() == '.pdf'),
                None
            )
            
            # Upload files
            print("  Uploading files...")
            self._upload_files(archived_files, default_preview_file=pdf_filename)

            # Update metadata
            print(f"  Updating metadata (version: {tag_name})...")
            self._update_metadata(tag_name)
            print("  ✓ Metadata updated")

            # Publish
            print("  Publishing...")
            published_record = self.new_draft_version.publish()
            doi = published_record.data["pids"]["doi"]["identifier"]
            record_html = published_record.data["links"]["self_html"]

            print(f"✓ Published to Zenodo!")
            print(f"  DOI: https://doi.org/{doi}")
            print(f"  URL: {record_html}")

            return doi

        except Exception as e:
            raise ZenodoError(f"Failed to publish new version: {e}")
        finally:
            self._reset()