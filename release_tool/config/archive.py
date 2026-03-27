"""Archive configuration: ArchiveConfig + ARCHIVE_OPTIONS."""

from .schema import ConfigOption
from .transform_common import _resolve_optional_path
from .common import COMMON_OPTIONS, CommonConfig
from .env import ConfigError


# ---------------------------------------------------------------------------
# Archive-specific options (CLI-only, no yaml_path)
# ---------------------------------------------------------------------------

ARCHIVE_OPTIONS: list[ConfigOption] = [
    ConfigOption("tag", env_key=None,
                 help="Git tag to archive"),
    ConfigOption("output_dir", env_key=None, nullable=True,
                 transform=_resolve_optional_path,
                 help="Output directory (default: temporary directory)"),
    ConfigOption("remote", env_key=None, nullable=True,
                 help="Git remote URL — perform a shallow clone "
                      "instead of using the local repo"),
    ConfigOption("no_cache", env_key=None, type="store_true", default=False,
                 help="Fetch the tag from the remote origin instead of "
                      "using the local repo "
                      "(useful when the tag has not been fetched locally)"),
]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_archive_context(config) -> None:
    """Check that archive has enough context to run."""
    if not config.remote and not config.project_root:
        raise ConfigError(
            "Cannot find project root (no .git directory found)",
            name="archive.no_project_root",
        )
    if not config.project_name_prefix:
        if config.project_root:
            config.project_name_prefix = config.project_root.name
        else:
            raise ConfigError(
                "--project-name-prefix is required when using --remote "
                "outside a git repository",
                name="archive.missing_prefix",
            )


# ---------------------------------------------------------------------------
# ArchiveConfig
# ---------------------------------------------------------------------------

class ArchiveConfig(CommonConfig):
    """Configuration for the archive command."""

    _options = COMMON_OPTIONS + ARCHIVE_OPTIONS
    _required: list[str] = ["tag"]
    _cli_aliases: dict[str, str] = {
        "archive_format": "format",
        "hash_algorithms": "hash-algo",
    }

    def __init__(self, project_root, yaml_config, env_vars, cli_overrides=None):
        super().__init__(project_root, yaml_config, env_vars, cli_overrides)
        validate_archive_context(self)
