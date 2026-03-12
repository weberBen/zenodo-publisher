"""Common configuration: base class and shared options."""

from pathlib import Path
from typing import Any

from .schema import ConfigOption
from .yaml import find_config_file, load_yaml_file, traverse_yaml
from .transform_common import (
    TREE_ALGORITHMS,
    PROJECT_NAME_TEMPLATE_VARS,
    _resolve_project_name_prefix,
    _validate_project_name_suffix,
    validate_hash_algorithms,
    _build_tar_args,
    _build_gzip_args,
)
from .env import (
    ConfigError,
    NotInitializedError,
    find_project_root,
    load_env,
    validate_env_keys,
    validate_type,
    validate_choices,
    SENSITIVE_ENV_KEYS,
)

# ---------------------------------------------------------------------------
# Common options (shared by all subcommands)
# ---------------------------------------------------------------------------

COMMON_OPTIONS: list[ConfigOption] = [
    ConfigOption("project_name_prefix", env_key=None,
                 yaml_path="project_name.prefix", default="",
                 transform=_resolve_project_name_prefix,
                 help="Project name prefix for display and file naming "
                      "(defaults to root dir name)"),
    ConfigOption("project_name_suffix", env_key=None,
                 yaml_path="project_name.suffix",
                 default="-{tag_name}",
                 validate=_validate_project_name_suffix,
                 help="Suffix template for file naming. "
                      "Available variables: {"
                      + "}, {".join(PROJECT_NAME_TEMPLATE_VARS) + "}"),
    ConfigOption("main_branch", env_key=None,
                 yaml_path="main_branch", default="main",
                 help="Git main branch name"),
    ConfigOption("debug", env_key=None,
                 yaml_path="debug", type="bool", default=False,
                 help="Enable debug mode (full stack traces)"),
    ConfigOption("archive_format", env_key=None,
                 yaml_path="archive.format", default="zip",
                 choices=["zip", "tar", "tar.gz"],
                 help="Archive format: zip, tar, or tar.gz"),
    ConfigOption("archive_tar_extra_args", env_key=None,
                 yaml_path="archive.tar_extra_args",
                 type="list", default="",
                 transform=_build_tar_args,
                 help="Extra args for tar (override defaults via dedup_args)"),
    ConfigOption("archive_gzip_extra_args", env_key=None,
                 yaml_path="archive.gzip_extra_args",
                 type="list", default="",
                 transform=_build_gzip_args,
                 help="Extra args for gzip (override defaults via dedup_args)"),
    ConfigOption("hash_algorithms", env_key=None,
                 yaml_path="hash_algorithms",
                 type="list", default="sha256",
                 validate=validate_hash_algorithms,
                 help="Hash algorithms (e.g. sha256,md5,tree). Uses hashlib or git tree hash"),
]


# ---------------------------------------------------------------------------
# CommonConfig base class
# ---------------------------------------------------------------------------


class CommonConfig:
    """Base configuration class.

    Options are defined in _options (single source of truth).
    Priority: CLI overrides > zenodo_config.yaml > .zenodo.env (sensitive) > defaults.

    Subclasses extend _options and may set:
      _required:    list of option names that must be non-None
      _cli_aliases: dict mapping option name -> short CLI flag name
    """

    _options: list[ConfigOption] = COMMON_OPTIONS
    _required: list[str] = []
    _cli_aliases: dict[str, str] = {}

    def __init__(
        self,
        project_root: Path | None,
        yaml_config: dict,
        env_vars: dict[str, str],
        cli_overrides: dict[str, Any] | None = None,
    ):
        self.project_root = project_root
        self.is_zp_project = (
            bool(yaml_config)  # --config override counts as initialized
            or (project_root is not None
                and find_config_file(project_root) is not None)
        )
        self.yaml_config = yaml_config
        cli_overrides = cli_overrides or {}

        debug = getattr(self, 'debug', False)

        if env_vars:
            validate_env_keys(env_vars, SENSITIVE_ENV_KEYS)

        for opt in self._options:
            try:
                raw = self._resolve_value(opt, yaml_config, env_vars, cli_overrides)
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

                if opt.validate:
                        validated = opt.validate(getattr(self, opt.name))
                        if validated == False: #True, None -> valide | False, exception -> invalid
                            raise Exception("")
            except Exception as e:
                if debug:
                    raise e

                error_msg = f"Invalid argument for '{opt.yaml_path or opt.env_key or opt.name}'"
                exception_msg = str(e)
                if exception_msg:
                    error_msg += f"\n{exception_msg}"
                raise ConfigError(error_msg)

        self.config_path = None
        self.config_path_overrided = False
        self._validate()

    def project_name_template(self, context: dict[str, str]) -> str:
        """Assemble full project name: prefix + resolved suffix.

        Raises KeyError if suffix uses a variable not in context.
        """
        suffix = self.project_name_suffix or ""
        if not suffix:
            return self.project_name_prefix
        resolved = suffix.format_map(context)

        return f"{self.project_name_prefix}", f"{resolved}"

    def generate_project_name(self, context: dict[str, str]) -> str:
        prefix, suffix = self.project_name_template(context)
        self.project_name = f"{prefix}{suffix}"
        self.project_name_template = [prefix, "", suffix] # prefix, delimiter, suffix

    def _validate(self) -> None:
        self._validate_required()

    def _validate_required(self) -> None:
        """Check that all required options have non-None values."""
        for name in self._required:
            if getattr(self, name, None) is None:
                raise ConfigError(f"Required option '{name}' not set")

    @classmethod
    def from_args(cls, args):
        """Build config from CLI args: discover project root, load YAML + env, extract overrides."""
        project_root = cls._discover_project_root(args)

        # --config: override YAML config file
        config_override = getattr(args, "config", None)
        if config_override:
            config_path = Path(config_override)
            config_path_overrided = True
            raise_exception = True
        else:
            config_path = find_config_file(project_root) if project_root else None
            config_path_overrided = False
            raise_exception = False

        yaml_config = load_yaml_file(config_path, raise_exception=raise_exception)
        if yaml_config is None:
            yaml_config = {}

        env_vars = cls._load_env_safe(project_root)
        cli_overrides = cls._extract_overrides(args)
        instance = cls(project_root, yaml_config, env_vars, cli_overrides)
        instance.config_path = config_path
        instance.config_path_overrided = config_path_overrided
        return instance

    @classmethod
    def _discover_project_root(cls, args) -> Path | None:
        """Find project root, returning None if not in a git repo."""
        try:
            return find_project_root()
        except RuntimeError:
            return None

    @classmethod
    def _load_env_safe(cls, project_root: Path | None) -> dict[str, str]:
        """Load .zenodo.env if available (sensitive vars only), return empty dict otherwise."""
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
        self, opt: ConfigOption, yaml_config: dict, env_vars: dict,
        cli_overrides: dict,
    ) -> Any:
        """Priority: CLI override > YAML config > env file > default."""
        if opt.name in cli_overrides and cli_overrides[opt.name] is not None:
            return cli_overrides[opt.name]
        if opt.yaml_path:
            val = traverse_yaml(yaml_config, opt.yaml_path)
            if val is not None:
                return val
        if opt.env_key and opt.env_key in env_vars:
            return env_vars[opt.env_key]
        return opt.default

    @staticmethod
    def _format_coerce_wrong_type_msg(defined_type, inferred_type, value):
        msg = f"value '{value}' looks like a '{inferred_type}' "
        msg += f"but type is '{defined_type}' (expected '{inferred_type}'?)"
        return msg

    def _coerce(self, opt: ConfigOption, value: Any) -> Any:
        """Coerce values to proper Python types.

        YAML already provides native types (bool, list, int).
        String coercion is only needed for env vars and CLI args.
        """
        if opt.parse:
            return opt.parse(opt, value)
        if value is None:
            return None

        # YAML native types: already correct Python type
        if opt.type == "bool" and isinstance(value, bool):
            return value
        if opt.type == "list" and isinstance(value, list):
            return value

        if not isinstance(value, str): # store_true, int, etc.
            return value

        # String coercion (from env vars or CLI)
        if opt.type == "bool":
            return (value.strip().lower() == "true")
        if opt.type == "list":
            return [t.strip() for t in value.split(",") if t.strip()]

        if opt.type == "str":
            canonical_value = value.strip().lower()
            if canonical_value in ["true", "false"]:
                raise ConfigError(
                    self._format_coerce_wrong_type_msg(opt.type, "bool", value)
                )
            if "," in canonical_value:
                raise ConfigError(
                    self._format_coerce_wrong_type_msg(opt.type, "list", value)
                )
            if canonical_value in ["none", "null", ""]:
                if opt.nullable:
                    return None
                if canonical_value == "":
                    return ""
                raise ConfigError("argument is not nullable")

        return value
