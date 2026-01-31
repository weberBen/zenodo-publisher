"""Zenodo operations for publishing releases."""

import json
import requests
from pathlib import Path
from typing import Optional


class ZenodoError(Exception):
    """Zenodo operation error."""
    pass

def get_latest_record_from_doi(
    access_token: str,
    doi: str,
    zenodo_api_url: str
    ):
    
    """
    Get the latest version record id from any version doi (including concept doi)
    """
    
    record_id = doi.split("zenodo.")[-1]
    
     # Get the latest version deposition ID from concept DOI
    headers = {"Authorization": f"Bearer {access_token}"}

    # Search for the record by concept DOI
    search_url = f"{zenodo_api_url}/records/{record_id}/versions/latest"

    response = requests.get(search_url, headers=headers)

    if response.status_code != 200:
        raise ZenodoError(
            f"Failed to find record with id {record_id}: "
            f"{response.status_code} {response.text}"
        )

    if not response.raw:
        raise ZenodoError(
            f"Failed to find record with id {record_id} (no data)"
        )
    
    data = response.json()
    if not data.get("id", None):
        raise ZenodoError(
            f"Failed to find record with id {record_id} (no id)"
        )

    return data

def get_draft_record(
    access_token: str,
    record_id: str,
    zenodo_api_url: str
    ):
    
    """
    Get the latest version record id from any version doi (including concept doi)
    """
    
     # Get the latest version deposition ID from concept DOI
    headers = {"Authorization": f"Bearer {access_token}"}

    # Search for the record by concept DOI
    search_url = f"{zenodo_api_url}/records/{record_id}/draft"
    response = requests.get(search_url, headers=headers)

    if response.status_code != 200:
        return None

    if not response.raw:
        return None
    
    data = response.json()
    if not data.get("id", None):
        return None

    if data.get("status", "") != "draft":
        return None

    return data

def does_record_exists(
    access_token: str,
    record_id: str,
    zenodo_api_url: str
) -> str:
    
    try:
        get_latest_record_from_doi(access_token, record_id, zenodo_api_url)
        return True
    except:
        return False

def discard_draft(
    access_token: str,
    record_id: str,
    zenodo_api_url: str
) -> str:
    # Get the latest version deposition ID from concept DOI
    headers = {"Authorization": f"Bearer {access_token}"}
    
    # Create new version
    new_version_url = f"{zenodo_api_url}/deposit/depositions/{record_id}/actions/discard"
    response = requests.post(new_version_url, headers=headers)
    # if response.status_code != 201:
    #     raise ZenodoError(
    #         f"Failed to discard draft: {response.status_code} {response.text}"
    #     )

def find_draft_record(access_token, concept_id, zenodo_api_url):
    if concept_id is None:
        return None
    
    headers = {'Authorization': f'Bearer {access_token}'}

    params = {
        'q': f'conceptdoi:"{concept_id}"',
    }
    
    r = requests.get(
        f'{zenodo_api_url}/deposit/depositions',
        headers=headers,
        params=params
    )
    
    if r.status_code != 200:
        return None
        
    results = r.json()
    
    if not results or len(results)==0:
        return None
    
    record = results[0]
    record_id = record["id"]
    
    if record["state"] != "unsubmitted":
        return []
    
    # check if unsubmitted record is draft
    draft_record = get_draft_record(access_token, record_id, zenodo_api_url)
    if not draft_record:
        return None
    if draft_record["conceptrecid"] != concept_id:
        return None
    
    return draft_record["id"]


def create_new_version(
    access_token: str,
    concept_doi: str,
    zenodo_api_url: str
) -> str:
    """
    Create a new version of an existing Zenodo deposit.

    Args:
        access_token: Zenodo access token
        concept_doi: Concept DOI of the existing record
        zenodo_api_url: Zenodo API base URL

    Returns:
        Deposition ID for the new version

    Raises:
        ZenodoError: If creation fails
    """
    # Get the latest version deposition ID from concept DOI
    headers = {"Authorization": f"Bearer {access_token}"}

    record_data = get_latest_record_from_doi(access_token, concept_doi, zenodo_api_url)
    record_id = record_data["id"]
    concept_id = record_data["conceptrecid"]
    
    draft_id = find_draft_record(access_token, concept_id, zenodo_api_url)
    if draft_id:
        print(f'\tDetecting draft (id={record_id}) for deposit {concept_id}: deleting...')
        discard_draft(access_token, draft_id, zenodo_api_url)
    else:
        print(f'\tNo draft for deposit {concept_id}')

    # Create new version
    new_version_url = f"{zenodo_api_url}/deposit/depositions/{record_id}/actions/newversion"
    response = requests.post(new_version_url, headers=headers)

    if response.status_code != 201:
        raise ZenodoError(
            f"Failed to create new version: {response.status_code} {response.text}"
        )

    # Get the new draft deposition ID
    new_version_data = response.json()
    deposition_id = new_version_data["id"]
    
    # make sure we get a draft
    deposition_record = get_draft_record(access_token, deposition_id, zenodo_api_url)
    if not deposition_record or (deposition_record["conceptrecid"] != concept_id):
        raise ZenodoError("Trying to edit existing version")

    return deposition_id


def delete_existing_files(
    access_token: str,
    deposition_id: str,
    zenodo_api_url: str
) -> None:
    """
    Delete all existing files from a Zenodo draft deposition.
    Draft (through API not web) is a copy of the previous record
    Thus all file are also copied and must be deleted before uploading
    new ones

    Args:create_new_version
        access_token: Zenodo access token
        deposition_id: Deposition ID
        zenodo_api_url: Zenodo API base URL

    Raises:
        ZenodoError: If deletion fails
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    # Get existing files
    files_url = f"{zenodo_api_url}/deposit/depositions/{deposition_id}/files"
    response = requests.get(files_url, headers=headers)

    if response.status_code != 200:
        raise ZenodoError(
            f"Failed to get files: {response.status_code} {response.text}"
        )

    files = response.json()

    # Delete each file
    for file in files:
        file_id = file["id"]
        delete_url = f"{zenodo_api_url}/deposit/depositions/{deposition_id}/files/{file_id}"
        response = requests.delete(delete_url, headers=headers)

        if response.status_code != 204:
            raise ZenodoError(
                f"Failed to delete file {file['filename']}: "
                f"{response.status_code} {response.text}"
            )


def upload_file(
    access_token: str,
    deposition_id: str,
    file_path: Path,
    zenodo_api_url: str,
) -> None:
    """
    Upload a file to a Zenodo deposition.

    Args:
        access_token: Zenodo access token
        deposition_id: Deposition ID
        file_path: Path to file to upload
        zenodo_api_url: Zenodo API base URL

    Raises:
        ZenodoError: If upload fails
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    upload_url = f"{zenodo_api_url}/deposit/depositions/{deposition_id}/files"

    with open(file_path, "rb") as f:
        files = {"file": f}
        data = {"name": file_path.name}

        response = requests.post(upload_url, headers=headers, data=data, files=files)

    if response.status_code != 201:
        raise ZenodoError(
            f"Failed to upload file: {response.status_code} {response.text}"
        )


def update_metadata(
    access_token: str,
    deposition_id: str,
    version: str,
    zenodo_api_url: str
) -> None:
    """
    Update the version metadata of a Zenodo deposition.

    Args:
        access_token: Zenodo access token
        deposition_id: Deposition ID
        version: Version string (e.g., tag name)
        zenodo_api_url: Zenodo API base URL

    Raises:
        ZenodoError: If update fails
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # Get current metadata
    deposition_url = f"{zenodo_api_url}/deposit/depositions/{deposition_id}"
    response = requests.get(deposition_url, headers=headers)

    if response.status_code != 200:
        raise ZenodoError(
            f"Failed to get metadata: {response.status_code} {response.text}"
        )

    current_data = response.json()

    # Update only the version field
    metadata = current_data["metadata"]
    metadata["version"] = version

    # Send update
    update_data = {"metadata": metadata}
    response = requests.put(
        deposition_url,
        headers=headers,
        data=json.dumps(update_data)
    )

    if response.status_code != 200:
        raise ZenodoError(
            f"Failed to update metadata: {response.status_code} {response.text}"
        )


def publish_deposition(
    access_token: str,
    deposition_id: str,
    zenodo_api_url: str
) -> dict:
    """
    Publish a Zenodo deposition.

    Args:
        access_token: Zenodo access token
        deposition_id: Deposition ID
        zenodo_api_url: Zenodo API base URL

    Returns:
        Published record data

    Raises:
        ZenodoError: If publication fails
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    publish_url = f"{zenodo_api_url}/deposit/depositions/{deposition_id}/actions/publish"
    response = requests.post(publish_url, headers=headers)

    if response.status_code != 202:
        raise ZenodoError(
            f"Failed to publish: {response.status_code} {response.text}"
        )

    return response.json()


def publish_new_version(
    archived_files: list[tuple[Path, str]],
    tag_name: str,
    access_token: str,
    concept_doi: str,
    zenodo_api_url: str
) -> str:
    """
    Publish a new version on Zenodo.

    Args:
        archived_files: List of tuples (file_path, md5_checksum) to upload
        tag_name: Tag name (used as version)
        access_token: Zenodo access token
        concept_doi: Concept DOI of existing record
        zenodo_api_url: Zenodo API base URL

    Returns:
        DOI of the published version

    Raises:
        ZenodoError: If publication fails
    """
    print(f"\n📤 Publishing new version to Zenodo...")
    print(f"  Concept DOI: {concept_doi}")
    print(f"  Version: {tag_name}")

    # Create new version
    print("  Creating new version...")
    deposition_id = create_new_version(access_token, concept_doi, zenodo_api_url)
    print(f"  ✓ New version created (ID: {deposition_id})")

    # Delete existing files from the draft
    print("  Removing files from old draft...")
    delete_existing_files(access_token, deposition_id, zenodo_api_url)
    print("  ✓ Old files removed")

    # Upload all files (PDF first for default preview on the old API)
    sorted_files = sorted(
        archived_files,
        key=lambda x: x[0].suffix.lower() != '.pdf'  # False (0) pour PDF, True (1) pour autres
    )

    for file_path, _ in sorted_files:
        print(f"  Uploading {file_path.name}...")
        upload_file(access_token, deposition_id, file_path, zenodo_api_url)
        print(f"  ✓ {file_path.name} uploaded")


    # Update metadata with version
    print(f"  Updating metadata (version: {tag_name})...")
    update_metadata(access_token, deposition_id, tag_name, zenodo_api_url)
    print("  ✓ Metadata updated")

    # Publish
    print("  Publishing...")
    result = publish_deposition(access_token, deposition_id, zenodo_api_url)
    doi = result["doi"]
    record_html = result["links"]["html"]

    print(f"✓ Published to Zenodo!")
    print(f"  DOI: https://doi.org/{doi}")
    print(f"  URL: {record_html}")

    return doi
