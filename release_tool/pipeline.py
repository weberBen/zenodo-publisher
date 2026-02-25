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

def prompt_user(prompt: str) -> str:
    """
    Prompt user for input.

    Args:
        prompt: Prompt message

    Returns:
        User input (stripped)
    """
    return input(f"{prompt}: ").strip()

def run_release(config) -> int:
    """Run the release process with the given config."""
    try:
        _run_release(config)
    except Exception as e:
        if config.debug:
            raise e
        print(f"\n💀❌💀 {RED_UNDERLINE}Error during process execution:{RESET} 💀❌💀\n{e}\n")
    except KeyboardInterrupt:
        print("\nExited.")

def _run_release(config) -> int:
    """
    Main release process.

    Returns:
        Exit code (0 for success, 1 for error)
    """

    if config.prompt_validation_level == "light":
        prompt_validation = "y/n"
        def validated_response(response, project_name):
            if not response or (response.lower() in ["Y", "y"]):
                return True
            return False
    else:
        prompt_validation = "Enter project name"
        def validated_response(response, project_name):
            if response and response.lower() == project_name:
                return True
            return False

    project_name = config.project_name

    print(f"✓ Project root: {config.project_root}")
    print(f"✓ Project name: {project_name}")
    print(f"✓ Main branch: {config.main_branch}")
    PROJECT_HOSTNAME = f"({RED_UNDERLINE}{project_name}{RESET})"

    if config.compile:
        # Build LaTeX
        response = prompt_user(
            f"{PROJECT_HOSTNAME} Start building project ? [{prompt_validation}]"
        )
        if not validated_response(response, project_name=project_name):
            print(f"{PROJECT_HOSTNAME}  ❌ Exit process.\nNothing done.")
            return

        print(f"{PROJECT_HOSTNAME} 📋 Starting build process...")

        compile(config.compile_dir, config.make_args)
    else:
        print(f"{PROJECT_HOSTNAME} ⚠️ Skipping project compilation (see config file)")

    # Check git status
    print(f"\n{PROJECT_HOSTNAME} 🔍 Checking git repository status...")
    check_on_main_branch(config.project_root, config.main_branch)
    print(f"{PROJECT_HOSTNAME} ✓ On {config.main_branch} branch")

    check_up_to_date(config.project_root, config.main_branch)

    # Check if latest commit already has a release
    is_released, latest_release = is_latest_commit_released(config.project_root)

    tag_name = latest_release['tagName']
    if is_released:
        print(
            f"\n✓ Latest commit already has a release: "
            f"{tag_name}"
        )
        print("✅ Nothing to do for release.")
    else:

        # Display latest release info
        print(f"\n{PROJECT_HOSTNAME} 📋 Current release status:")
        if latest_release:
            print(f"  Last release: {latest_release['tagName']}")
            if latest_release.get('name'):
                print(f"  Title: {latest_release['name']}")
            if latest_release.get('body'):
                # Show first 100 chars of release notes
                body = latest_release['body']
                body_preview = body[:100] + "..." if len(body) > 100 else body
                print(f"  Notes: {body_preview}")
        else:
            print("  No releases found (this will be the first release)")

        # Prompt for new release
        print(f"\n{PROJECT_HOSTNAME} 📝 Creating new release...")
        while True:
            new_tag = prompt_user(f"{PROJECT_HOSTNAME} Enter new tag name")
            if new_tag:
                break
            print("Tag name cannot be empty")

        release_title = prompt_user(
            f"{PROJECT_HOSTNAME} Enter release title (press Enter to use '{new_tag}')"
        )
        if not release_title:
            release_title = new_tag
            print(f"Using default title: {release_title}")

        release_notes = prompt_user(
            f"{PROJECT_HOSTNAME} Enter release notes (press Enter to skip)"
        )
        if not release_notes:
            release_notes = ""
            print("No release notes provided")

        # Verify tag validity before creating release
        print(f"\n{PROJECT_HOSTNAME} 🔍 Verifying tag validity...")
        check_tag_validity(config.project_root, new_tag, config.main_branch)

        # Create GitHub release (automatically creates tag and pushes)
        create_github_release(
            config.project_root,
            new_tag,
            release_title,
            release_notes
        )

        # Final verification
        print("\n🔍 Final verification...")
        check_up_to_date(config.project_root, config.main_branch)
        verify_release_on_latest_commit(config.project_root, new_tag)

        print(f"\n{PROJECT_HOSTNAME} ✅ Release {tag_name} completed successfully!")

        tag_name = new_tag

    # Rename files
    archived_files = archive(config, tag_name)

    # GPG signing
    if config.gpg_sign:
        signatures = sign_files(archived_files, compute_md5, gpg_uid=config.gpg_uid, armor=config.gpg_armor, overwrite=config.gpg_overwrite, extra_args=config.gpg_extra_args)
        archived_files.extend(signatures)

    print(f"\n{PROJECT_HOSTNAME} ✅ Archived files:")

    for entry in archived_files:
        print(f"   • {entry['file_path'].name}")
        print(f"     MD5: {entry['md5']}")
        print(f"     (persist: {entry['persist']})")

    # Publish to Zenodo if configured
    if not config.has_zenodo_config():
        print(f"\n\n{PROJECT_HOSTNAME} ⚠️  No publisher set")
        return

    publisher = ZenodoPublisher(config)

    up_to_date, msg = publisher.is_up_to_date(tag_name, archived_files)
    if msg:
        print(f"\n{PROJECT_HOSTNAME} ✅ {msg}")
    if not up_to_date:
        pass
    elif up_to_date and not config.force_zenodo_update:
        print("\nNo publication made.")
        return
    else:
        print(f"\n\n{PROJECT_HOSTNAME} ⚠️ Forcing zenodo update")
        pass


    response = prompt_user(
        f"{PROJECT_HOSTNAME} Publish version ? [{prompt_validation}]"
    )
    if not validated_response(response, project_name=project_name):
        print(f"{PROJECT_HOSTNAME} ❌ Exit process.\n⚠️ No publication made")
        return

    try:
        zenodo_doi, zenodo_url = publisher.publish_new_version(archived_files, tag_name)
        print(f"  Zenodo DOI: {zenodo_doi}")
        print(f"\n{PROJECT_HOSTNAME} ✅ Publication {tag_name} completed successfully!")

        if config.zenodo_info_to_release:
            info_path = add_zenodo_asset_to_release(
                config.project_root,
                tag_name,
                zenodo_doi,
                zenodo_url,
                archived_files,
                debug=config.debug
            )
            print(f"  Zenodo publication info file: {info_path}")

    except ZenodoError as e:
        print(f"\n{PROJECT_HOSTNAME} ⚠️  GitHub release created but Zenodo publication failed: {e}", file=sys.stderr)
        print(f"  You can manually upload files to Zenodo")
        return
