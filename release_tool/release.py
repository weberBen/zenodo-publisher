"""Main release logic."""

import sys
from .config import Config, NotInitializedError
from .latex_build import build_latex
from .git_operations import (
    check_on_main_branch,
    check_up_to_date,
    is_latest_commit_released,
    check_tag_validity,
    create_github_release,
    verify_release_on_latest_commit,
    GitError,
    GitHubError,
)
from .zenodo_operations import ZenodoPublisher, ZenodoError
from .archive_operation import archive

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


def run_release(
    safeguard_prompt_response: bool
    ) -> int:
    """
    Main release process.

    Returns:
        Exit code (0 for success, 1 for error)
    """

    # Load configuration
    print("⚙️  Loading configuration...")
    try:
        config = Config()
    except NotInitializedError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        return
    
    print(f"✓ Project root: {config.project_root}")
    print(f"✓ Main branch: {config.main_branch}")
    
    project_name = config.project_root.name
    PROJECT_HOSTNAME = f"({RED_UNDERLINE}{project_name}{RESET})"
    
    prompt_validation = "enter project name" if safeguard_prompt_response else "y/n"
    start_process = prompt_user(
        f"{PROJECT_HOSTNAME} Start process ? {prompt_validation}"
    )
    if not safeguard_prompt_response and start_process and (start_process.lower in ["n", "no"]):
        print("{PROJECT_HOSTNAME}  ❌ Exit process.\nNothing done.")
        return
    elif (not start_process) or (start_process.lower() != project_name):
        print("❌ Exit process.\nNothing done.")
        return 

    # Build LaTeX
    print(f"{PROJECT_HOSTNAME} 📋 Starting latex build process...")
    build_latex(config.latex_dir)

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
            new_tag = prompt_user("{PROJECT_HOSTNAME} Enter new tag name (e.g., v1.0.0) ")
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

    # Rename PDF
    archived_files = archive(config, tag_name)   
    print(f"\n{PROJECT_HOSTNAME} ✅ Archived files:")
    for file_path, md5 in archived_files:
        print(f"   • {file_path.name}")
        print(f"     MD5: {md5}")
    
    # Publish to Zenodo if configured
    if not config.has_zenodo_config():
        print(f"\n\n{PROJECT_HOSTNAME} ⚠️  No publisher set")
        return

    publisher = ZenodoPublisher(
        config.zenodo_token,
        config.zenodo_api_url,
        config.zenodo_concept_doi,
        config.publication_date
    )

    up_to_date, msg = publisher.is_up_to_date(tag_name, archived_files)
    if msg:
        print(f"\n{PROJECT_HOSTNAME} ✅ {msg}")
    if up_to_date:
        return

    prompt_validation = "enter project name" if safeguard_prompt_response else "y/n"
    release_title = prompt_user(
        f"{PROJECT_HOSTNAME} Publish version (enter publish) ? [{prompt_validation}]"
    )
    if not safeguard_prompt_response and start_process and (start_process.lower in ["n", "no"]):
        print(f"{PROJECT_HOSTNAME} ❌ Exit process.\n⚠️ No publication made")
        return
    if (not release_title) or (release_title.lower() != project_name):
        print(f"{PROJECT_HOSTNAME} ❌ Exit process.\n⚠️ No publication made")
        return

    try:
        zenodo_doi = publisher.publish_new_version(archived_files, tag_name)
        print(f"  Zenodo DOI: {zenodo_doi}")
        print(f"\n{PROJECT_HOSTNAME} ✅ Publication {tag_name} completed successfully!")

    except ZenodoError as e:
        print(f"\n{PROJECT_HOSTNAME} ⚠️  GitHub release created but Zenodo publication failed: {e}", file=sys.stderr)
        print(f"  You can manually upload files to Zenodo")
        return
