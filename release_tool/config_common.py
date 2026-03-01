"""Common configuration: base class and shared options."""

from pathlib import Path
from typing import Any

from .config_schema import ConfigOption
from .config_transform_common import (
    _resolve_project_name,
    _build_tar_args,
    _build_gzip_args
)
from .config_env import (
    ConfigError,
    NotInitializedError,
    find_project_root,
    load_env,
    validate_env_keys,
    validate_type,
    validate_choices,
)

# ---------------------------------------------------------------------------
# Common options (shared by all subcommands)
# ---------------------------------------------------------------------------

COMMON_OPTIONS: list[ConfigOption] = [
    ConfigOption("project_name", "PROJECT_NAME", default="",
                 transform=_resolve_project_name,
                 help="Project name for display and file naming (defaults to root dir name)"),
    ConfigOption("main_branch", "MAIN_BRANCH", default="main",
                 help="Git main branch name"),
    ConfigOption("debug", "DEBUG", type="bool", default=False,
                 help="Enable debug mode (full stack traces)"),
    ConfigOption("archive_format", "ARCHIVE_FORMAT", default="zip",
                 choices=["zip", "tar", "tar.gz"],
                 help="Archive format: zip, tar, or tar.gz"),
    ConfigOption("archive_tar_extra_args", "ARCHIVE_TAR_EXTRA_ARGS",
                 type="list", default="",
                 transform=_build_tar_args,
                 help="Extra args for tar (override defaults via dedup_args)"),
    ConfigOption("archive_gzip_extra_args", "ARCHIVE_GZIP_EXTRA_ARGS",
                 type="list", default="",
                 transform=_build_gzip_args,
                 help="Extra args for gzip (override defaults via dedup_args)"),
    ConfigOption("zenodo_identifier_hash_algorithms",
                 "ZENODO_IDENTIFIER_HASH_ALGORITHMS",
                 type="list", default="sha256",
                 help="Hash algorithms for identifiers (e.g. sha256,md5,sha512). Uses hashlib"),
]


# ---------------------------------------------------------------------------
# CommonConfig base class
# ---------------------------------------------------------------------------

class CommonConfig:
    """Base configuration class.

    Options are defined in _options (single source of truth).
    Priority: CLI overrides > .zenodo.env > defaults.

    Subclasses extend _options and may set:
      _required:    list of option names that must be non-None
      _cli_aliases: dict mapping option name -> short CLI flag name
    """

    _options: list[ConfigOption] = COMMON_OPTIONS
    _required: list[str] = []
    _cli_aliases: dict[str, str] = {}
    _all_env_keys: set[str] = set()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for o in cls._options:
            if o.env_key:
                CommonConfig._all_env_keys.add(o.env_key)

    def __init__(
        self,
        project_root: Path | None,
        env_vars: dict[str, str],
        cli_overrides: dict[str, Any] | None = None,
    ):
        self.project_root = project_root
        self.is_zp_project = (
            project_root is not None
            and (project_root / ".zenodo.env").exists()
        )
        cli_overrides = cli_overrides or {}

        if env_vars:
            validate_env_keys(env_vars, CommonConfig._all_env_keys)

        for opt in self._options:
            raw = self._resolve_value(opt, env_vars, cli_overrides)
            validate_type(opt, raw)
            value = self._coerce(opt, raw)
            validate_choices(opt, value)

            if opt.transform:
                result = opt.transform(value, project_root)
                if opt.extra_attrs:
                    setattr(self, opt.name, result[0])
                    for i, attr_name in enumerate(opt.extra_attrs):
                        setattr(self, attr_name, result[i + 1])
                else:
                    setattr(self, opt.name, result)
            else:
                setattr(self, opt.name, value)

        self._validate_required()

    def _validate_required(self) -> None:
        """Check that all required options have non-None values."""
        for name in self._required:
            if getattr(self, name, None) is None:
                raise ConfigError(f"Required option '{name}' not set")

    @classmethod
    def from_args(cls, args):
        """Build config from CLI args: discover project root, load env, extract overrides."""
        project_root = cls._discover_project_root(args)
        env_vars = cls._load_env_safe(project_root)
        cli_overrides = cls._extract_overrides(args)
        return cls(project_root, env_vars, cli_overrides)

    @classmethod
    def _discover_project_root(cls, args) -> Path | None:
        """Find project root, returning None if not in a git repo."""
        try:
            return find_project_root()
        except RuntimeError:
            return None

    @classmethod
    def _load_env_safe(cls, project_root: Path | None) -> dict[str, str]:
        """Load .zenodo.env if available, return empty dict otherwise."""
        if not project_root:
            return {}
        try:
            return load_env(project_root)
        except (RuntimeError, NotInitializedError):
            return {}

    @classmethod
    def _extract_overrides(cls, args) -> dict[str, Any]:
        """Extract CLI overrides from argparse namespace."""
        overrides = {}
        for opt in cls._options:
            if not opt.cli:
                continue
            val = getattr(args, opt.name, None)
            if val is not None:
                overrides[opt.name] = val
        return overrides

    def _resolve_value(
        self, opt: ConfigOption, env_vars: dict, cli_overrides: dict,
    ) -> Any:
        """Priority: CLI override > env file > default."""
        if opt.name in cli_overrides and cli_overrides[opt.name] is not None:
            return cli_overrides[opt.name]
        if opt.env_key and opt.env_key in env_vars:
            return env_vars[opt.env_key]
        return opt.default

    def _coerce(self, opt: ConfigOption, value: Any) -> Any:
        """Coerce string values from env file to proper Python types."""
        if value is None:
            return None
        if opt.type == "bool" and isinstance(value, str):
            return value.lower() == "true"
        if opt.type == "list" and isinstance(value, str):
            return [t.strip() for t in value.split(",") if t.strip()]
        if opt.type == "optional_str" and isinstance(value, str):
            return value if value.strip() else None
        return value


# Register common env keys at module level
for _o in COMMON_OPTIONS:
    if _o.env_key:
        CommonConfig._all_env_keys.add(_o.env_key)
