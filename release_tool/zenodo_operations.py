"""Zenodo operations for publishing releases."""

import json
import requests
from pathlib import Path
from typing import Optional


class ZenodoError(Exception):
    """Zenodo operation error."""
    pass


def create_new_version(
    access_token: str,
    concept_doi: str,
    zenodo_api_url: str = "https://zenodo.org/api"
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

    # Search for the record by concept DOI
    search_url = f"{zenodo_api_url}/records/{concept_doi}"

    response = requests.get(search_url, headers=headers)

    if response.status_code != 200:
        raise ZenodoError(
            f"Failed to find record with concept DOI {concept_doi}: "
            f"{response.status_code} {response.text}"
        )

    data = response.json()
    if not data.get("id", None):
        raise ZenodoError(f"No record found with concept DOI {concept_doi}")

    # Get the record ID directly from the response
    record_id = data["id"]

    # Create new version
    new_version_url = f"{zenodo_api_url}/deposit/depositions/{record_id}/actions/newversion"
    response = requests.post(new_version_url, headers=headers)

    if response.status_code != 201:
        raise ZenodoError(
            f"Failed to create new version: {response.status_code} {response.text}"
        )

    # Get the new draft deposition ID
    new_version_data = response.json()
    latest_draft_url = new_version_data["links"]["latest_draft"]

    # Extract deposition ID from the URL
    deposition_id = latest_draft_url.split("/")[-1]

    return deposition_id


def delete_existing_files(
    access_token: str,
    deposition_id: str,
    zenodo_api_url: str = "https://zenodo.org/api"
) -> None:
    """
    Delete all existing files from a Zenodo deposition.

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
    zenodo_api_url: str = "https://zenodo.org/api"
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
    zenodo_api_url: str = "https://zenodo.org/api"
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
    zenodo_api_url: str = "https://zenodo.org/api"
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
    project_root: Path,
    pdf_path: Path,
    tag_name: str,
    access_token: str,
    concept_doi: str,
    zenodo_api_url: str = "https://zenodo.org/api"
) -> str:
    """
    Publish a new version on Zenodo.

    Args:
        project_root: Path to project root
        pdf_path: Path to PDF file to upload
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

    # Upload new files
    print(f"  Uploading {pdf_path.name}...")
    upload_file(access_token, deposition_id, pdf_path, zenodo_api_url)
    print("  ✓ File uploaded")

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
