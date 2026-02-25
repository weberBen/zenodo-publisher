"""Configuration schema: single source of truth for config options and CLI args."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class ConfigOption:
    """Describes a single configuration option.

    Used to auto-generate both config loading from .zenodo.env
    and CLI arguments from argparse.
    """
    name: str                  # Config attribute name: "gpg_sign"
    env_key: str | None        # Env var key: "GPG_SIGN" (None = not in .zenodo.env)
    type: str = "str"          # "str", "bool", "optional_str", "list"
    default: Any = None
    cli: bool = True           # False to hide from CLI (e.g. ZENODO_TOKEN)
    help: str = ""
    required: bool = False
    transform: Callable | None = None   # (value, project_root) -> value
    extra_attrs: list[str] = field(default_factory=list)


# --- Transform functions ---

def _parse_main_file(value, project_root):
    """Split 'main.pdf' into ('main', 'pdf')."""
    parts = value.split(".")
    return parts[0], ".".join(parts[1:])


def _resolve_compile_dir(value, project_root):
    """Resolve COMPILE_DIR relative to project_root."""
    return project_root / value


def _resolve_optional_path(value, project_root):
    """Parse optional path, return None if empty."""
    return Path(value) if value else None


def _strip_or_none(value, project_root):
    """Strip whitespace, return None if empty."""
    v = value.strip() if value else ""
    return v if v else None


def _resolve_project_name(value, project_root):
    """Return value if non-empty, otherwise project root directory name."""
    v = value.strip() if value else ""
    return v if v else project_root.name


# --- Options registry ---

OPTIONS: list[ConfigOption] = [
    # Required settings
    ConfigOption("project_name", "PROJECT_NAME", default="",
                 transform=_resolve_project_name,
                 help="Project name for display and file naming (defaults to root dir name)"),
    ConfigOption("main_branch", "MAIN_BRANCH", default="main",
                 help="Git main branch name"),
    ConfigOption("compile_dir", "COMPILE_DIR", default="",
                 transform=_resolve_compile_dir,
                 help="Compile directory (relative to project root)"),
    ConfigOption("main_file", "MAIN_FILE", default="main.pdf",
                 transform=_parse_main_file,
                 extra_attrs=["main_file_extension"],
                 help="Main file name with extension (e.g. main.pdf)"),
    ConfigOption("compile", "COMPILE", type="bool", default=True,
                 help="Enable project compilation"),

    # Zenodo configuration
    ConfigOption("publisher_type", "PUBLISHER_TYPE", type="optional_str",
                 help="Publisher type (zenodo)"),
    ConfigOption("zenodo_token", "ZENODO_TOKEN", default="",
                 cli=False,
                 help="Zenodo API token"),
    ConfigOption("zenodo_concept_doi", "ZENODO_CONCEPT_DOI", default="",
                 help="Zenodo concept DOI"),
    ConfigOption("zenodo_api_url", "ZENODO_API_URL",
                 default="https://zenodo.org/api",
                 help="Zenodo API base URL"),
    ConfigOption("publication_date", "PUBLICATION_DATE", type="optional_str",
                 help="Publication date (YYYY-MM-DD), defaults to today UTC"),
    ConfigOption("zenodo_info_to_release", "ZENODO_INFO_TO_RELEASE",
                 type="bool", default=False,
                 help="Add Zenodo info JSON to GitHub release"),

    # Archive options
    ConfigOption("archive_types", "ARCHIVE_TYPES", type="list",
                 default="project",
                 help="Comma-separated archive types (pdf, project)"),
    ConfigOption("persist_types", "PERSIST_TYPES", type="list", default="",
                 help="Comma-separated types to persist to archive dir"),
    ConfigOption("archive_dir", "ARCHIVE_DIR", type="optional_str",
                 transform=_resolve_optional_path,
                 help="Directory for persistent archives"),

    # GPG signing
    ConfigOption("gpg_sign", "GPG_SIGN", type="bool", default=False,
                 help="Enable GPG signing of archives"),
    ConfigOption("gpg_uid", "GPG_UID", type="optional_str",
                 transform=_strip_or_none,
                 help="GPG key UID (empty = system default)"),
    ConfigOption("gpg_armor", "GPG_ARMOR", type="bool", default=True,
                 help="ASCII-armored GPG signatures (.asc)"),
    ConfigOption("gpg_overwrite", "GPG_OVERWRITE", type="bool", default=False,
                 help="Overwrite existing GPG signature files"),

    # Runtime options (formerly CLI-only)
    ConfigOption("debug", "DEBUG", type="bool", default=False,
                 help="Enable debug mode (full stack traces)"),
    ConfigOption("prompt_validation_level", "PROMPT_VALIDATION_LEVEL",
                 default="strict",
                 help="Prompt validation level: strict or light"),
    ConfigOption("force_zenodo_update", "FORCE_ZENODO_UPDATE",
                 type="bool", default=False,
                 help="Force Zenodo update even if up to date"),
]
