"""Release configuration: ReleaseConfig + RELEASE_OPTIONS."""

from .config_schema import ConfigOption
from .config_transform_common import _resolve_optional_path
from .config_transform_release import (
    PERSIST_SPECIAL_TYPES,
    _parse_main_file,
    _resolve_compile_dir,
    _strip_or_none,
    _build_gpg_args,
    _dedup_make_args,
    _validate_commit_fields,
)
from .config_common import COMMON_OPTIONS, CommonConfig, validate_hash_algorithms
from .config_env import ConfigError


# ---------------------------------------------------------------------------
# Release-specific options
# ---------------------------------------------------------------------------

RELEASE_OPTIONS: list[ConfigOption] = [
    ConfigOption("compile_dir", "COMPILE_DIR", default="",
                 transform=_resolve_compile_dir,
                 help="Compile directory (relative to project root)"),
    ConfigOption("main_file", "MAIN_FILE", default="main.pdf",
                 transform=_parse_main_file,
                 extra_attrs=["main_file_extension"],
                 help="Main file name with extension (e.g. main.pdf)"),
    ConfigOption("compile", "COMPILE", type="bool", default=True,
                 help="Enable project compilation"),
    ConfigOption("make_args", "MAKE_ARGS", type="list", default="",
                 transform=_dedup_make_args,
                 help="Extra args passed to make (e.g. -j4,VERBOSE=1)"),

    # Zenodo configuration
    ConfigOption("publisher_type", "PUBLISHER_TYPE",
                 choices=["zenodo"],
                 help="Publisher type (zenodo)"),
    ConfigOption("zenodo_token", "ZENODO_TOKEN", default="",
                 cli=False,
                 help="Zenodo API token"),
    ConfigOption("zenodo_concept_doi", "ZENODO_CONCEPT_DOI", default="",
                 help="Zenodo concept DOI"),
    ConfigOption("zenodo_api_url", "ZENODO_API_URL",
                 default="https://zenodo.org/api",
                 help="Zenodo API base URL"),
    ConfigOption("publication_date", "PUBLICATION_DATE", nullable=True,
                 help="Publication date (YYYY-MM-DD), defaults to today UTC"),
    # Manifest
    ConfigOption("manifest", "MANIFEST", type="bool", default=True,
                 help="Generate manifest JSON listing all archives with hashes"),
    ConfigOption("manifest_identifier_hash", "MANIFEST_IDENTIFIER_HASH",
                 default="sha256",
                 validate=validate_hash_algorithms,
                 help="Algorithm to hash the manifest for Zenodo alternate identifier"),
    ConfigOption("manifest_metadata_fields", "MANIFEST_METADATA_FIELDS",
                 type="list", default="",
                 help="Metadata fields from .zenodo.json to include in manifest "
                      "(e.g. title,creators)"),
    ConfigOption("manifest_commit_fields", "MANIFEST_COMMIT_FIELDS",
                 type="list", default="sha,date_epoch",
                 validate=_validate_commit_fields,
                 help="Commit fields in manifest: sha,date_epoch,subject,"
                      "author_name,author_email,branch,origin"),
    ConfigOption("manifest_to_release", "MANIFEST_TO_RELEASE",
                 type="bool", default=True,
                 help="Upload manifest to GitHub release"),

    # Archive options
    ConfigOption("archive_types", "ARCHIVE_TYPES", type="list",
                 default="project",
                 help="Comma-separated archive types (pdf, project)"),
    ConfigOption("persist_types", "PERSIST_TYPES", type="list", default="manifest",
                 help="Types to persist to archive dir: *extension"
                      + ", ".join(PERSIST_SPECIAL_TYPES)),
    ConfigOption("archive_dir", "ARCHIVE_DIR", nullable=True,
                 transform=_resolve_optional_path,
                 help="Directory for persistent archives"),

    # GPG signing
    ConfigOption("gpg_sign", "GPG_SIGN", type="bool", default=False,
                 help="Enable GPG signing of archives"),
    ConfigOption("gpg_uid", "GPG_UID", nullable=True,
                 transform=_strip_or_none,
                 help="GPG key UID (empty = system default)"),
    ConfigOption("gpg_extra_args", "GPG_EXTRA_ARGS", type="list",
                 default=",".join(["--armor"]),
                 transform=_build_gpg_args,
                 help="Extra args passed to gpg (use --no-armor for binary .sig)"),
    ConfigOption("gpg_sign_support", "GPG_SIGN_SUPPORT",
                 choices=["file", "file_hash"],
                 help="Precise the gpg support for signature (e.g. sign the file, sign the hash's file, ...) "),
    ConfigOption("gpg_sign_support_hash", "GPG_SIGN_SUPPORT_HASH", type="optional_str",
                 default="sha256",
                 validate=validate_hash_algorithms,
                 help="Precise the gpg support for signature (e.g. sign the file, sign the hash's file, ...) "),

    # Runtime options
    ConfigOption("prompt_validation_level", "PROMPT_VALIDATION_LEVEL",
                 default="light",
                 choices=["danger", "light", "normal", "secure"],
                 help="Prompt validation level: "
                      "danger (Enter to confirm), "
                      "light (y/yes), "
                      "normal (yes/no in full), "
                      "secure (type project root name)"),
    ConfigOption("zenodo_force_update", "ZENODO_FORCE_UPDATE",
                 type="bool", default=False,
                 help="Force Zenodo update even if up to date"),
]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_compile_dir(config) -> None:
    """Check that compile_dir exists if set."""
    if config.compile_dir and not config.compile_dir.exists():
        raise ConfigError(
            f"Compile directory not found: {config.compile_dir}"
        )

def validate_project_root(config) -> None:
    project_root = config.project_root
    if not project_root:
        raise ConfigError("No project root defined")
    if not project_root.exists():
        raise ConfigError(f"Invalid project root {project_root}")

def validate(config):
    validate_project_root(config)
    validate_compile_dir(config)

# ---------------------------------------------------------------------------
# ReleaseConfig
# ---------------------------------------------------------------------------

class ReleaseConfig(CommonConfig):
    """Configuration for the release command."""

    _options = COMMON_OPTIONS + RELEASE_OPTIONS
    _required: list[str] = []
    _cli_aliases: dict[str, str] = {}

    def __init__(self, project_root, env_vars, cli_overrides=None):
        super().__init__(project_root, env_vars, cli_overrides)
        validate(self)

    def has_zenodo_config(self) -> bool:
        """Check if Zenodo configuration is complete."""
        return self.publisher_type is not None
