"""Main release logic."""

from .latex_build import compile
from .git_operations import (
    check_on_main_branch,
    check_up_to_date,
    is_latest_commit_released,
    check_tag_validity,
    create_github_release,
    verify_release_on_latest_commit,
    add_zenodo_asset_to_release,
    GitError,
    GitHubError,
)
from .zenodo_operations import ZenodoPublisher, ZenodoError
from .archive_operation import archive, compute_md5
from .gpg_operations import sign_files
from . import output


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


def _step_compile(config, hint, validator):
    """Compile project via make (with user prompt)."""
    if not config.compile:
        output.step_warn("Skipping project compilation (see config file)")
        return

    if not _confirm("Start building project ?", hint, validator, config.project_name):
        raise RuntimeError("Build aborted by user.")

    output.step("📋 Starting build process...")
    compile(config.compile_dir, config.make_args)


def _step_archive(config, tag_name) -> list:
    """Create archives and optionally GPG-sign them. Returns archived file list."""
    archived_files = archive(config, tag_name)

    if config.gpg_sign:
        signatures = sign_files(
            archived_files, compute_md5,
            gpg_uid=config.gpg_uid,
            overwrite=config.gpg_overwrite,
            extra_args=config.gpg_extra_args,
        )
        archived_files.extend(signatures)

    output.step_ok("Archived files:")
    for entry in archived_files:
        output.detail(f"• {entry['file_path'].name}")
        output.detail(f"  MD5: {entry['md5']}")
        output.detail(f"  persist: {entry['persist']}")

    return archived_files


def _step_zenodo(config, tag_name, archived_files, hint, validator):
    """Check Zenodo state and publish if needed."""
    if not config.has_zenodo_config():
        output.step_warn("No publisher set")
        return

    publisher = ZenodoPublisher(config)

    up_to_date, msg = publisher.is_up_to_date(tag_name, archived_files)
    if msg:
        output.step_ok(msg)
    if up_to_date and not config.zenodo_force_update:
        output.info("No publication made.")
        return
    if up_to_date:
        output.step_warn("Forcing zenodo update")

    if not _confirm("Publish version ?", hint, validator, config.project_name):
        output.warn("No publication made")
        return

    try:
        zenodo_doi, zenodo_url = publisher.publish_new_version(archived_files, tag_name)
        output.detail(f"Zenodo DOI: {zenodo_doi}")
        output.step_ok(f"Publication {tag_name} completed successfully!")

        if config.zenodo_info_to_release:
            info_path = add_zenodo_asset_to_release(
                config.project_root, tag_name,
                zenodo_doi, zenodo_url,
                archived_files, debug=config.debug,
            )
            output.detail(f"Zenodo publication info file: {info_path}")

    except ZenodoError as e:
        output.error(f"GitHub release created but Zenodo publication failed: {e}")
        output.detail("You can manually upload files to Zenodo")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run_release(config) -> None:
    """Run the release process with the given config."""
    try:
        _run_release(config)
    except Exception as e:
        if config.debug:
            raise
        output.fatal("Error during process execution:")
        output.error(str(e))
    except KeyboardInterrupt:
        output.info("\nExited.")


def _run_release(config) -> None:
    """Main release pipeline."""
    output.setup(config.project_name, config.debug)
    hint, validator = _make_validator(config.prompt_validation_level)

    output.info_ok(f"Project root: {config.project_root}")
    output.info_ok(f"Project name: {config.project_name}")
    output.info_ok(f"Main branch: {config.main_branch}")

    # 1. Git check
    _step_git_check(config)

    # 2. Release check/creation
    tag_name = _step_release(config)

    # 3. Compile
    _step_compile(config, hint, validator)

    # 4. Re-check git + release still valid after compilation
    _step_git_check(config)
    verify_release_on_latest_commit(config.project_root, tag_name)

    # 5. Archive + sign
    archived_files = _step_archive(config, tag_name)

    # 6. Zenodo publish
    _step_zenodo(config, tag_name, archived_files, hint, validator)
