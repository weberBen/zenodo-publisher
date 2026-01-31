"""Main release logic."""

import sys
import shutil
from pathlib import Path
from .config import Config
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
from .zenodo_operations import publish_new_version, ZenodoError


def prompt_user(prompt: str) -> str:
    """
    Prompt user for input.

    Args:
        prompt: Prompt message

    Returns:
        User input (stripped)
    """
    return input(f"{prompt}: ").strip()


def rename_pdf(latex_dir: Path, base_name: str, tag_name: str) -> Path:
    """
    Rename main.pdf to {base_name}-{tag_name}.pdf.

    Args:
        latex_dir: Path to LaTeX directory
        base_name: Base name for the PDF
        tag_name: Tag name (version)

    Returns:
        Path to renamed PDF file

    Raises:
        FileNotFoundError: If main.pdf doesn't exist
    """
    main_pdf = latex_dir / "main.pdf"
    if not main_pdf.exists():
        raise FileNotFoundError(
            f"main.pdf not found at {main_pdf}\n"
            f"Make sure LaTeX build completed successfully"
        )

    new_name = f"{base_name}-{tag_name}.pdf"
    new_pdf = latex_dir / new_name

    print(f"\n📝 Renaming PDF: main.pdf → {new_name}")
    shutil.copy2(main_pdf, new_pdf)
    print(f"✓ PDF renamed to {new_name}")

    return new_pdf


def run_release() -> int:
    """
    Main release process.

    Returns:
        Exit code (0 for success, 1 for error)
    """

    # Load configuration
    print("⚙️  Loading configuration...")
    config = Config()
    print(f"✓ Project root: {config.project_root}")
    print(f"✓ Main branch: {config.main_branch}")

    # Build LaTeX
    build_latex(config.latex_dir)

    # Check git status
    print("\n🔍 Checking git repository status...")
    check_on_main_branch(config.project_root, config.main_branch)
    print(f"✓ On {config.main_branch} branch")

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
        print("\n📋 Current release status:")
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
        print("\n📝 Creating new release...")
        while True:
            new_tag = prompt_user("Enter new tag name (e.g., v1.0.0)")
            if new_tag:
                break
            print("Tag name cannot be empty")

        release_title = prompt_user(
            f"Enter release title (press Enter to use '{new_tag}')"
        )
        if not release_title:
            release_title = new_tag
            print(f"Using default title: {release_title}")

        release_notes = prompt_user(
            "Enter release notes (press Enter to skip)"
        )
        if not release_notes:
            release_notes = ""
            print("No release notes provided")

        # Verify tag validity before creating release
        print("\n🔍 Verifying tag validity...")
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
        
        print(f"\n✅ Release {tag_name} completed successfully!")
        
        tag_name = new_tag

    # Rename PDF
    renamed_pdf = rename_pdf(config.latex_dir, config.base_name, tag_name)   
    print(f"\n✅ PDF {renamed_pdf.name} available at {renamed_pdf}")
    
    
    release_title = prompt_user(
        f"Publish version (enter publish) ? [publish/no]"
    )
    if release_title != "publish":
        print(f"No publication made")
        return
        
    # Publish to Zenodo if configured
    if not config.has_zenodo_config():
        print("\n\n⚠️  No publisher set")
        return
    
    try:
        zenodo_doi = publish_new_version(
            config.project_root,
            renamed_pdf,
            tag_name,
            config.zenodo_token,
            config.zenodo_concept_doi,
            config.zenodo_api_url
        )
        
        print(f"  Zenodo DOI: {zenodo_doi}")
        print(f"\n✅ Publication {tag_name} completed successfully!")
        
    except ZenodoError as e:
        print(f"\n⚠️  GitHub release created but Zenodo publication failed: {e}", file=sys.stderr)
        print(f"  You can manually upload {renamed_pdf.name} to Zenodo")
        return
        

    # except (GitError, GitHubError, RuntimeError, FileNotFoundError, ValueError) as e:
    #     print(f"\n❌ Error: {e}", file=sys.stderr)
    #     return 1
    # except KeyboardInterrupt:
    #     print("\n\n⚠️  Release cancelled by user")
    #     return 1
    # except Exception as e:
    #     print(f"\n❌ Unexpected error: {e}", file=sys.stderr)
    #     return 1
