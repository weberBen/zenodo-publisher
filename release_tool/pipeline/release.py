"""Main release logic."""

import json
import tempfile
from pathlib import Path

from ..latex_build import compile
from ..git_operations import (
    check_on_main_branch,
    check_up_to_date,
    is_latest_commit_released,
    check_tag_validity,
    create_github_release,
    verify_release_on_latest_commit,
    get_last_commit_info,
    get_release_asset_digest,
    upload_release_asset,
)
from ..zenodo_operations import ZenodoPublisher, ZenodoError
from ..archive_operation import (
    archive, compute_file_hash, compute_hashes,
    generate_manifest, manifest_to_file,
)
from ..file_utils import persist_files
from ..gpg_operations import sign_files
from .. import output
from ._common import setup_pipeline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prompt(msg: str) -> str:
    return input(f"{msg}: ").strip()


def _make_validator(level: str):
    """Return (hint_text, validator_fn) based on prompt validation level."""
    if level == "light":
        return "y/n", lambda resp, _name: not resp or resp.lower() in ("y", "yes")
    return "Enter project name", lambda resp, _name: bool(resp) and resp.lower() == _name


def _confirm(message: str, hint: str, validator, project_name: str) -> bool:
    """Prompt user for confirmation. Returns True if confirmed."""
    response = _prompt(f"{message} [{hint}]")
    if not validator(response, project_name):
        step_abort()
        return False
    return True


def step_abort():
    output.step_warn("Exit process.")

def ellipse_hash(hash_str, visible_char=8):
    hash_str = hash_str.split(":")[-1]
    return f"{hash_str[:visible_char]}...{hash_str[-visible_char:]}"

# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _step_git_check(config):
    """Check branch, remote sync, and local modifications."""
    output.step("🔍 Checking git repository status...")
    check_on_main_branch(config.project_root, config.main_branch)
    output.step_ok(f"On {config.main_branch} branch")
    check_up_to_date(config.project_root, config.main_branch)


def _step_release(config) -> str:
    """Check or create a GitHub release. Returns the tag name."""
    is_released, latest_release = is_latest_commit_released(config.project_root)

    if is_released:
        tag_name = latest_release["tagName"]
        output.info_ok(f"Latest commit already has a release: {tag_name}")
        output.info_ok("Nothing to do for release.")
        return tag_name

    # Display previous release info
    output.step("📋 Current release status:")
    if latest_release:
        output.detail(f"Last release: {latest_release['tagName']}")
        if latest_release.get("name"):
            output.detail(f"Title: {latest_release['name']}")
        if latest_release.get("body"):
            body = latest_release["body"]
            preview = body[:100] + "..." if len(body) > 100 else body
            output.detail(f"Notes: {preview}")
    else:
        output.detail("No releases found (this will be the first release)")

    # Prompt for new tag / title / notes
    output.step("📝 Creating new release...")
    while True:
        new_tag = _prompt("Enter new tag name")
        if new_tag:
            break
        output.warn("Tag name cannot be empty")

    release_title = _prompt(
        f"Enter release title (press Enter to use '{new_tag}')"
    )
    if not release_title:
        release_title = new_tag
        output.detail(f"Using default title: {release_title}")

    release_notes = _prompt("Enter release notes (press Enter to skip)")
    if not release_notes:
        release_notes = ""
        output.detail("No release notes provided")

    # Validate and create
    output.step("🔍 Verifying tag validity...")
    check_tag_validity(config.project_root, new_tag, config.main_branch)

    create_github_release(config.project_root, new_tag, release_title, release_notes)

    output.step_ok(f"Release {new_tag} created successfully!")
    return new_tag

def _step_commit_info(config, tag_name):
    commit_env = get_last_commit_info(config.project_root, tag_name=tag_name)
    output.info_ok(f"Commit SHA: {commit_env['ZP_COMMIT_SHA']}")
    output.info_ok(f"Commit timestamp: {commit_env['ZP_COMMIT_DATE_EPOCH']}")
    output.info_ok(f"Commit subject: {commit_env['ZP_COMMIT_SUBJECT']}")
    output.info_ok(f"Author: {commit_env['ZP_COMMIT_AUTHOR_NAME']} <{commit_env['ZP_COMMIT_AUTHOR_EMAIL']}>")
    output.info_ok(f"Committer: {commit_env['ZP_COMMIT_COMMITTER_NAME']} <{commit_env['ZP_COMMIT_COMMITTER_EMAIL']}>")
    output.info_ok(f"Branch: {commit_env['ZP_BRANCH']}")
    output.info_ok(f"Origin: {commit_env['ZP_ORIGIN_URL']}")

    return commit_env

def _step_compile(config, hint, validator, env_vars=None):
    """Compile project via make (with user prompt)."""
    if not config.compile:
        output.step_warn("Skipping project compilation (see config file)")
        return

    if not _confirm("Start building project ?", hint, validator, config.project_name):
        raise RuntimeError("Build aborted by user.")

    output.step("📋 Starting build process...")
    compile(config.compile_dir, config.make_args, env_vars=env_vars)


def _step_archive(config, tag_name, output_dir) -> list:
    """Create archives and compute checksums. Returns archived_files."""
    archived_files = archive(config, tag_name, output_dir)

    output.step_ok("Archived files:")
    for entry in archived_files:
        output.detail(f"• {entry['file_path'].name}")
        for algo, h in entry["hashes"].items():
            output.detail(f"  {algo}: {h['value']}")
        output.detail(f"  persist: {entry['persist']}")

    return archived_files


def _step_manifest(config, tag_name, archived_files, commit_env, output_dir) -> tuple[dict, dict | None]:
    """Generate manifest, compute its identifier hash, optionally GPG-sign it.

    Returns (manifest dict, identifier dict or None).
    The manifest file + optional signature are appended to archived_files.
    """
    if not config.manifest:
        return None, None

    output.step("📋 Generating manifest...")

    # Load optional metadata fields from .zenodo.json
    metadata = _load_manifest_metadata(config)

    manifest = generate_manifest(
        archived_files, tag_name, commit_env,
        commit_fields=config.manifest_commit_fields, metadata=metadata,
    )
    manifest_path = manifest_to_file(manifest, tag_name, output_dir)
    output.detail(f"Manifest: {manifest_path}")

    # Hash the manifest → Zenodo identifier
    algo = config.manifest_identifier_hash
    identifier = compute_file_hash(manifest_path, algo)
    output.detail(f"Identifier: {identifier['formatted_value']}")

    # GPG sign the manifest (not individual archives)
    if config.gpg_sign:
        signatures = sign_files(
            [{"file_path": manifest_path, "filename": "manifest", "persist": False}],
            output_dir,
            gpg_uid=config.gpg_uid,
            overwrite=config.gpg_overwrite,
            extra_args=config.gpg_extra_args,
        )
        for sig in signatures:
            sig["persist"] = "sig" in config.persist_types
        compute_hashes(signatures, config.hash_algorithms)
        archived_files.extend(signatures)

    # Add manifest itself to the upload list
    manifest_entry = {
        "file_path": manifest_path,
        "is_preview": False,
        "filename": "manifest",
        "extension": "json",
        "type": "manifest",
        "persist": "manifest" in config.persist_types,
        "is_signature": False,
    }
    compute_hashes([manifest_entry], config.hash_algorithms)
    archived_files.append(manifest_entry)

    output.step_ok("Manifest generated")
    return manifest, identifier


def _load_manifest_metadata(config) -> dict | None:
    """Extract metadata fields from .zenodo.json for inclusion in manifest."""
    fields = config.manifest_metadata_fields
    if not fields:
        return None

    zenodo_json = config.project_root / ".zenodo.json"
    if not zenodo_json.exists():
        return None

    with open(zenodo_json) as f:
        data = json.load(f)
    source = data.get("metadata", data)

    metadata = {}
    for field in fields:
        if field in source:
            metadata[field] = source[field]
    return metadata or None


def _step_zenodo(config, tag_name, archived_files, identifier, hint, validator) -> dict | None:
    """Check Zenodo state and publish if needed. Returns record_info dict or None."""
    if not config.has_zenodo_config():
        output.step_warn("No publisher set")
        return None

    output.step("Zenodo process...")

    publisher = ZenodoPublisher(config)

    up_to_date, msg, record_info = publisher.is_up_to_date(tag_name, archived_files)
    if up_to_date and record_info:
        output.info(f"Last record url: https://doi.org/{record_info['doi']}")
        output.info(f"Last record url: {record_info['record_url']}")

    if msg:
        output.step_ok(msg)
    if up_to_date and not config.zenodo_force_update:
        output.info("No publication made.")
        return record_info
    if up_to_date:
        output.step_warn("Forcing zenodo update")

    if not _confirm("Publish version ?", hint, validator, config.project_name):
        output.warn("No publication made")
        return record_info

    try:
        record_info = publisher.publish_new_version(
            archived_files, tag_name, identifier=identifier,
        )
        output.detail(f"Zenodo DOI: {record_info['doi']}")
        output.step_ok(f"Publication {tag_name} completed successfully!")
        return record_info

    except ZenodoError as e:
        output.error(f"GitHub release created but Zenodo publication failed: {e}")
        output.detail("You can manually upload files to Zenodo")
    finally:
        return record_info


def _step_manifest_to_release(config, tag_name, manifest, manifest_path,
                               record_info, hint, validator):
    """Inject Zenodo info into manifest and upload to GitHub release."""
    if not config.manifest_to_release:
        return
    if manifest is None:
        return

    output.step("Checking manifest to release...")

    local_sha = compute_file_hash(manifest_path, "sha256")["formatted_value"]
    remote_sha = get_release_asset_digest(
        config.project_root, tag_name, manifest_path.name,
    )

    output.detail(f"Manifest: {manifest_path}")
    output.detail(f"Hash {local_sha}")

    if remote_sha and local_sha == remote_sha:
        output.step_ok("Manifest already up to date on release")
        return

    if remote_sha:
        output.step_warn("Manifest differs from release asset")
        output.detail(f"Remote: {ellipse_hash(remote_sha)}")
        output.detail(f"Local: {ellipse_hash(local_sha)}")
        if not _confirm("Overwrite existing manifest on release ?", hint, validator, config.project_name):
            output.warn("Manifest not updated on release")
            return

    upload_release_asset(config.project_root, tag_name, manifest_path, clobber=bool(remote_sha))
    output.step_ok("Manifest uploaded to release")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run_release(config) -> None:
    """Run the release process with the given config."""
    try:
        _run_release(config)
    except KeyboardInterrupt:
        output.info("\nExited.")
    except Exception as e:
        if config.debug:
            raise
        output.fatal("Error during process execution:")
        output.error(str(e))


def _run_release(config) -> None:
    """Main release pipeline."""
    setup_pipeline(config.project_name, config.debug, config.project_root)
    hint, validator = _make_validator(config.prompt_validation_level)

    output.info_ok(f"Main branch: {config.main_branch}")

    # Git check
    _step_git_check(config)

    # Release check/creation
    tag_name = _step_release(config)

    # Commit info
    commit_env = _step_commit_info(config, tag_name)

    # Compile
    _step_compile(config, hint, validator, env_vars=commit_env)

    # Re-check git + release still valid after compilation
    _step_git_check(config)
    verify_release_on_latest_commit(config.project_root, tag_name)

    # Working directory for all generated files
    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)

        # Archive
        archived_files = _step_archive(config, tag_name, output_dir)

        # Manifest (generates manifest, signs it if gpg_sign, appends to archived_files)
        manifest, identifier = _step_manifest(config, tag_name, archived_files, commit_env, output_dir)

        # Zenodo publish (archives + manifest + signature)
        record_info = _step_zenodo(config, tag_name, archived_files, identifier, hint, validator)

        # Upload manifest to release (with Zenodo info injected)
        manifest_path = next(
            (e["file_path"] for e in archived_files if e.get("type") == "manifest"), None
        )
        _step_manifest_to_release(config, tag_name, manifest, manifest_path,
                                    record_info, hint, validator)

        # Persist files to archive_dir/tag_name
        persist_files(archived_files, config.archive_dir, tag_name)
