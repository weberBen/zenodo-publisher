"""Archive configuration: ArchiveConfig + ARCHIVE_OPTIONS."""

from .config_schema import ConfigOption
from .config_transform_common import _resolve_optional_path
from .config_common import COMMON_OPTIONS, CommonConfig
from .config_env import ConfigError


# ---------------------------------------------------------------------------
# Archive-specific options (CLI-only, no env_key)
# ---------------------------------------------------------------------------

ARCHIVE_OPTIONS: list[ConfigOption] = [
    ConfigOption("tag", None,
                 help="Git tag to archive"),
    ConfigOption("output_dir", None, type="optional_str",
                 transform=_resolve_optional_path,
                 help="Output directory (default: temporary directory)"),
    ConfigOption("remote", None, type="optional_str",
                 help="Git remote URL — perform a shallow clone "
                      "instead of using the local repo"),
    ConfigOption("no_cache", None, type="store_true", default=False,
                 help="Fetch the tag from the remote origin instead of "
                      "using the local repo "
                      "(useful when the tag has not been fetched locally)"),
    ConfigOption("hash", None,
                 help="Additional hash algorithms, comma-separated "
                      "(e.g. sha512,tree,tree256)"),
]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_archive_context(config) -> None:
    """Check that archive has enough context to run."""
    if not config.remote and not config.project_root:
        raise ConfigError(
            "Cannot find project root (no .git directory found)"
        )
    if not config.project_name:
        if config.project_root:
            config.project_name = config.project_root.name
        else:
            raise ConfigError(
                "--project-name is required when using --remote outside a git repository"
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

    def __init__(self, project_root, env_vars, cli_overrides=None):
        super().__init__(project_root, env_vars, cli_overrides)
        validate_archive_context(self)
