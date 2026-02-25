"""Main release logic."""

import sys
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

RED_UNDERLINE = "\033[91;4m"
RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project_label(project_name: str) -> str:
    return f"({RED_UNDERLINE}{project_name}{RESET})"


def _prompt(msg: str) -> str:
    return input(f"{msg}: ").strip()


def _make_validator(level: str):
    """Return (hint_text, validator_fn) based on prompt validation level."""
    if level == "light":
        return "y/n", lambda resp, _name: not resp or resp.lower() in ("y", "yes")
    return "Enter project name", lambda resp, _name: bool(resp) and resp.lower() == _name


def _confirm(label: str, message: str, hint: str, validator, project_name: str) -> bool:
    """Prompt user for confirmation. Returns True if confirmed."""
    response = _prompt(f"{label} {message} [{hint}]")
    if not validator(response, project_name):
        print(f"{label} ❌ Exit process.\nNothing done.")
        return False
    return True


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def _step_git_check(config, label):
    """Check branch, remote sync, and local modifications."""
    print(f"\n{label} 🔍 Checking git repository status...")
    check_on_main_branch(config.project_root, config.main_branch)
    print(f"{label} ✓ On {config.main_branch} branch")
    check_up_to_date(config.project_root, config.main_branch)


def _step_release(config, label) -> str:
    """Check or create a GitHub release. Returns the tag name."""
    is_released, latest_release = is_latest_commit_released(config.project_root)

    if is_released:
        tag_name = latest_release["tagName"]
        print(f"\n✓ Latest commit already has a release: {tag_name}")
        print("✅ Nothing to do for release.")
        return tag_name

    # Display previous release info
    print(f"\n{label} 📋 Current release status:")
    if latest_release:
        print(f"  Last release: {latest_release['tagName']}")
        if latest_release.get("name"):
            print(f"  Title: {latest_release['name']}")
        if latest_release.get("body"):
            body = latest_release["body"]
            preview = body[:100] + "..." if len(body) > 100 else body
            print(f"  Notes: {preview}")
    else:
        print("  No releases found (this will be the first release)")

    # Prompt for new tag / title / notes
    print(f"\n{label} 📝 Creating new release...")
    while True:
        new_tag = _prompt(f"{label} Enter new tag name")
        if new_tag:
            break
        print("Tag name cannot be empty")

    release_title = _prompt(
        f"{label} Enter release title (press Enter to use '{new_tag}')"
    )
    if not release_title:
        release_title = new_tag
        print(f"Using default title: {release_title}")

    release_notes = _prompt(f"{label} Enter release notes (press Enter to skip)")
    if not release_notes:
        release_notes = ""
        print("No release notes provided")

    # Validate and create
    print(f"\n{label} 🔍 Verifying tag validity...")
    check_tag_validity(config.project_root, new_tag, config.main_branch)

    create_github_release(config.project_root, new_tag, release_title, release_notes)

    print(f"\n{label} ✅ Release {new_tag} created successfully!")
    return new_tag


def _step_compile(config, label, hint, validator):
    """Compile project via make (with user prompt)."""
    if not config.compile:
        print(f"{label} ⚠️ Skipping project compilation (see config file)")
        return

    if not _confirm(label, "Start building project ?", hint, validator, config.project_name):
        raise RuntimeError("Build aborted by user.")

    print(f"{label} 📋 Starting build process...")
    compile(config.compile_dir, config.make_args)


def _step_archive(config, tag_name, label) -> list:
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

    print(f"\n{label} ✅ Archived files:")
    for entry in archived_files:
        print(f"   • {entry['file_path'].name}")
        print(f"     MD5: {entry['md5']}")
        print(f"     (persist: {entry['persist']})")

    return archived_files


def _step_zenodo(config, tag_name, archived_files, label, hint, validator):
    """Check Zenodo state and publish if needed."""
    if not config.has_zenodo_config():
        print(f"\n\n{label} ⚠️  No publisher set")
        return

    publisher = ZenodoPublisher(config)

    up_to_date, msg = publisher.is_up_to_date(tag_name, archived_files)
    if msg:
        print(f"\n{label} ✅ {msg}")
    if up_to_date and not config.force_zenodo_update:
        print("\nNo publication made.")
        return
    if up_to_date:
        print(f"\n\n{label} ⚠️ Forcing zenodo update")

    if not _confirm(label, "Publish version ?", hint, validator, config.project_name):
        print(f"⚠️ No publication made")
        return

    try:
        zenodo_doi, zenodo_url = publisher.publish_new_version(archived_files, tag_name)
        print(f"  Zenodo DOI: {zenodo_doi}")
        print(f"\n{label} ✅ Publication {tag_name} completed successfully!")

        if config.zenodo_info_to_release:
            info_path = add_zenodo_asset_to_release(
                config.project_root, tag_name,
                zenodo_doi, zenodo_url,
                archived_files, debug=config.debug,
            )
            print(f"  Zenodo publication info file: {info_path}")

    except ZenodoError as e:
        print(
            f"\n{label} ⚠️  GitHub release created but Zenodo publication failed: {e}",
            file=sys.stderr,
        )
        print("  You can manually upload files to Zenodo")


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
        print(f"\n💀❌💀 {RED_UNDERLINE}Error during process execution:{RESET} 💀❌💀\n{e}\n")
    except KeyboardInterrupt:
        print("\nExited.")


def _run_release(config) -> None:
    """Main release pipeline."""
    label = _project_label(config.project_name)
    hint, validator = _make_validator(config.prompt_validation_level)

    print(f"✓ Project root: {config.project_root}")
    print(f"✓ Project name: {config.project_name}")
    print(f"✓ Main branch: {config.main_branch}")

    # 1. Git check
    _step_git_check(config, label)

    # 2. Release check/creation
    tag_name = _step_release(config, label)

    # 3. Compile
    _step_compile(config, label, hint, validator)

    # 4. Re-check git + release still valid after compilation
    _step_git_check(config, label)
    verify_release_on_latest_commit(config.project_root, tag_name)

    # 5. Archive + sign
    archived_files = _step_archive(config, tag_name, label)

    # 6. Zenodo publish
    _step_zenodo(config, tag_name, archived_files, label, hint, validator)
