"""Main release logic."""

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
    build_zenodo_info_json,
    upload_release_asset,
)
from ..zenodo_operations import ZenodoPublisher, ZenodoError
from ..archive_operation import archive, compute_md5, compute_sha256
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

def _step_commit_info(config):
    commit_env = get_last_commit_info(config.project_root)
    output.info_ok(f"Commit SHA: {commit_env['ZP_COMMIT_SHA']}")
    output.info_ok(f"Commit timestamp: {commit_env['ZP_COMMIT_DATE_EPOCH']}")
    output.info_ok(f"Commit subject: {commit_env['ZP_COMMIT_SUBJECT']}")
    output.info_ok(f"Author: {commit_env['ZP_COMMIT_AUTHOR_NAME']} <{commit_env['ZP_COMMIT_AUTHOR_EMAIL']}>")
    output.info_ok(f"Committer: {commit_env['ZP_COMMIT_COMMITTER_NAME']} <{commit_env['ZP_COMMIT_COMMITTER_EMAIL']}>")

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


def _step_archive(config, tag_name) -> tuple[list, list | None]:
    """Create archives and optionally GPG-sign them. Returns (archived_files, identifiers)."""
    archived_files, identifiers = archive(config, tag_name)

    if config.gpg_sign:
        signatures = sign_files(
            archived_files, compute_md5, compute_sha256,
            gpg_uid=config.gpg_uid,
            overwrite=config.gpg_overwrite,
            extra_args=config.gpg_extra_args,
        )
        archived_files.extend(signatures)

    output.step_ok("Archived files:")
    for entry in archived_files:
        output.detail(f"• {entry['file_path'].name}")
        output.detail(f"  MD5: {entry['md5']}")
        output.detail(f"  SHA256: {entry['sha256']}")
        output.detail(f"  persist: {entry['persist']}")


    if identifiers:
        types_label = '+'.join(set(config.zenodo_identifier_types))
        output.detail(f"\n-> Identifiers ({types_label} *{identifiers[0]['description']})")
        for ident in identifiers:
            output.detail(f"\t{ident['formatted_value']}")

    return archived_files, identifiers


def _step_zenodo(config, tag_name, archived_files, identifiers, hint, validator) -> dict | None:
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
            archived_files, tag_name, identifiers=identifiers,
        )
        output.detail(f"Zenodo DOI: {record_info['doi']}")
        output.step_ok(f"Publication {tag_name} completed successfully!")
        return record_info

    except ZenodoError as e:
        output.error(f"GitHub release created but Zenodo publication failed: {e}")
        output.detail("You can manually upload files to Zenodo")
    finally:
        return record_info


def _step_zenodo_info_to_release(config, tag_name, archived_files, identifiers,
                                  record_info, hint, validator):
    """Generate and upload zenodo_publication_info.json to the GitHub release."""
    if not config.zenodo_info_to_release:
        return
    if not config.has_zenodo_config():
        return

    if not record_info or not record_info.get("doi") or not record_info.get("record_url"):
        output.step_warn("No Zenodo DOI/URL available, skipping info file")
        return

    output.step("Generating zenodo publication info file...")
    # Build the JSON file
    info_path = build_zenodo_info_json(
        record_info["doi"], record_info["record_url"], archived_files,
        identifiers=identifiers, debug=config.debug,
    )

    # Compare with existing asset on the release
    local_sha = f"sha256:{compute_sha256(info_path)}"
    remote_sha = get_release_asset_digest(
        config.project_root, tag_name, info_path.name,
    )

    output.detail(f"Zenodo publication info file: {info_path}")
    output.detail(f"Hash {local_sha}")

    if remote_sha and local_sha == remote_sha:
        output.step_ok("Zenodo info file already up to date on release")
        return

    if remote_sha:
        output.step_warn(f"Zenodo info file differs from release asset")
        output.detail(f"Remote: {ellipse_hash(remote_sha)}")
        output.detail(f"Local: {ellipse_hash(local_sha)}")
        if not _confirm("Overwrite existing zenodo info on release ?", hint, validator, config.project_name):
            output.warn("Zenodo info not updated on release")
            return

    output.detail(f"Uploading zenodo publication info to release '{tag_name}'...")
    upload_release_asset(config.project_root, tag_name, info_path, clobber=bool(remote_sha))
    output.step_ok("Zenodo publication info added to release")


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

    # Commit info (timestand, hash)
    commit_env = _step_commit_info(config)
    commit_env = {
        **commit_env,
        "ZP_BRANCH": config.main_branch,
        "ZP_COMMIT_TAG": tag_name,
    }

    # Compile
    _step_compile(config, hint, validator, env_vars=commit_env)

    # Re-check git + release still valid after compilation
    _step_git_check(config)
    verify_release_on_latest_commit(config.project_root, tag_name)

    # Archive + sign
    archived_files, identifiers = _step_archive(config, tag_name)

    # Zenodo publish
    record_info = _step_zenodo(config, tag_name, archived_files, identifiers, hint, validator)

    # Zenodo info to release
    _step_zenodo_info_to_release(config, tag_name, archived_files, identifiers,
                                  record_info, hint, validator)
