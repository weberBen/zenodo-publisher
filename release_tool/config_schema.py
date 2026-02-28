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


def dedup_args(default_args: list[str], user_args: list[str]) -> list[str]:
    """Merge default and user args, last value wins for same key.

    --no-X in user_args removes --X from defaults (not passed to subprocess).

    Handles: --flag/--no-flag, --key=value, -Xvalue, KEY=value.
    """
    def _arg_key(arg):
        if arg.startswith("--"):
            return arg.split("=")[0][2:]   # --armor → armor, --key=val → key
        if arg.startswith("-") and len(arg) > 2:
            return arg[:2]                 # -j4 → -j
        if "=" in arg:
            return arg.split("=")[0]       # VERBOSE=1 → VERBOSE
        return arg

    seen = {}
    order = []
    for arg in default_args + user_args:
        if arg.startswith("--no-"):
            # --no-X removes --X from defaults
            key = arg[5:]
            if key in seen:
                order.remove(key)
                del seen[key]
            continue
        key = _arg_key(arg)
        if key not in seen:
            order.append(key)
        seen[key] = arg
    return [seen[k] for k in order]


TREE_ALGORITHMS = {"tree": "sha1", "tree256": "sha256"}

_GPG_DEFAULT_ARGS = ["--armor"]
_MAKE_DEFAULT_ARGS = []
# for reproductibility
_TAR_DEFAULT_ARGS = [
    "--sort=name", "--format=posix",
    "--pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime",
    "--mtime=1970-01-01 00:00:00Z",
    "--numeric-owner", "--owner=0", "--group=0",
    "--mode=go+u,go-w",
]
_GZIP_DEFAULT_ARGS = ["--no-name", "--best"]


def _build_gpg_args(value, project_root):
    """Merge default GPG args with user args."""
    return dedup_args(_GPG_DEFAULT_ARGS, value or [])


def _build_tar_args(value, project_root):
    """Merge default TAR args with user args."""
    result = dedup_args(_TAR_DEFAULT_ARGS, value or [])
    if result != _TAR_DEFAULT_ARGS:
        import warnings
        warnings.warn(
            "Custom tar args detected — this may affect archive reproducibility",
            stacklevel=2,
        )
    return result


def _build_gzip_args(value, project_root):
    """Merge default GZIP args with user args."""
    result = dedup_args(_GZIP_DEFAULT_ARGS, value or [])
    if result != _GZIP_DEFAULT_ARGS:
        import warnings
        warnings.warn(
            "Custom gzip args detected — this may affect archive reproducibility",
            stacklevel=2,
        )
    return result


def _dedup_make_args(value, project_root):
    """Dedup make args."""
    return dedup_args(_MAKE_DEFAULT_ARGS, value or [])


# --- Options registry ---

@dataclass
class CLIOption:
    """A pure CLI argument (not backed by .zenodo.env)."""
    name: str
    type: str = "str"       # "str", "bool", "store_true"
    default: Any = None
    required: bool = False
    help: str = ""
    metavar: str | None = None


# ConfigOption names shared across all subcommands (added via _add_common_flags).
COMMON_FLAG_NAMES: set[str] = {"debug"}


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
    ConfigOption("make_args", "MAKE_ARGS", type="list", default="",
                 transform=_dedup_make_args,
                 help="Extra args passed to make (e.g. -j4,VERBOSE=1)"),

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
    ConfigOption("zenodo_identifier_hash", "ZENODO_IDENTIFIER_HASH",
                 type="bool", default=False,
                 help="Add SHA256 hash as alternate identifier in Zenodo metadata"),
    ConfigOption("zenodo_identifier_types", "ZENODO_IDENTIFIER_TYPES",
                 type="list", default="",
                 help="File types to include in identifier hash (e.g. pdf,project). If multiple, hashes are concatenated"),
    ConfigOption("zenodo_identifier_hash_algorithms", "ZENODO_IDENTIFIER_HASH_ALGORITHMS",
                 type="list", default="sha256",
                 help="Hash algorithms for identifiers (e.g. sha256,md5,sha512). Uses hashlib"),

    # Archive options
    ConfigOption("archive_types", "ARCHIVE_TYPES", type="list",
                 default="project",
                 help="Comma-separated archive types (pdf, project)"),
    ConfigOption("persist_types", "PERSIST_TYPES", type="list", default="",
                 help="Comma-separated types to persist to archive dir"),
    ConfigOption("archive_dir", "ARCHIVE_DIR", type="optional_str",
                 transform=_resolve_optional_path,
                 help="Directory for persistent archives"),
    ConfigOption("archive_format", "ARCHIVE_FORMAT", default="zip",
                 help="Archive format: zip, tar, or tar.gz"),
    ConfigOption("archive_tar_extra_args", "ARCHIVE_TAR_EXTRA_ARGS",
                 type="list", default=",".join(_TAR_DEFAULT_ARGS),
                 transform=_build_tar_args,
                 help="Extra args for tar (override defaults via dedup_args)"),
    ConfigOption("archive_gzip_extra_args", "ARCHIVE_GZIP_EXTRA_ARGS",
                 type="list", default=",".join(_GZIP_DEFAULT_ARGS),
                 transform=_build_gzip_args,
                 help="Extra args for gzip (override defaults via dedup_args)"),

    # GPG signing
    ConfigOption("gpg_sign", "GPG_SIGN", type="bool", default=False,
                 help="Enable GPG signing of archives"),
    ConfigOption("gpg_uid", "GPG_UID", type="optional_str",
                 transform=_strip_or_none,
                 help="GPG key UID (empty = system default)"),
    ConfigOption("gpg_overwrite", "GPG_OVERWRITE", type="bool", default=False,
                 help="Overwrite existing GPG signature files"),
    ConfigOption("gpg_extra_args", "GPG_EXTRA_ARGS", type="list", default=",".join(_GPG_DEFAULT_ARGS),
                 transform=_build_gpg_args,
                 help="Extra args passed to gpg (use --no-armor for binary .sig)"),

    # Runtime options (formerly CLI-only)
    ConfigOption("debug", "DEBUG", type="bool", default=False,
                 help="Enable debug mode (full stack traces)"),
    ConfigOption("prompt_validation_level", "PROMPT_VALIDATION_LEVEL",
                 default="strict",
                 help="Prompt validation level: strict or light"),
    ConfigOption("zenodo_force_update", "ZENODO_FORCE_UPDATE",
                 type="bool", default=False,
                 help="Force Zenodo update even if up to date"),
]


ARCHIVE_CLI_OPTIONS: list[CLIOption] = [
    CLIOption("tag", required=True,
              help="Git tag to archive"),
    CLIOption("project_name",
              help="Project name for archive prefix "
                   "(default: from .zenodo.env or git root dir name). "
                   "Required when using --remote outside a git repository"),
    CLIOption("output_dir",
              help="Output directory (default: temporary directory)"),
    CLIOption("remote", metavar="URL",
              help="Git remote URL \u2013 perform a shallow clone "
                   "instead of using the local repo"),
    CLIOption("no_cache", type="store_true", default=False,
              help="Fetch the tag from the remote origin instead of "
                   "using the local repo "
                   "(useful when the tag has not been fetched locally)"),
    CLIOption("format",
              help="Archive format: zip, tar, or tar.gz (overrides config)"),
    CLIOption("hash",
              help="Additional hash algorithms, comma-separated "
                   "(e.g. sha512,tree,tree256)"),
    CLIOption("tar_extra_args",
              help="Extra tar args, comma-separated (override defaults)"),
    CLIOption("gzip_extra_args",
              help="Extra gzip args, comma-separated (override defaults)"),
]
