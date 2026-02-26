"""Configuration management for the release tool."""

from pathlib import Path
from typing import Any, Optional

from .config_schema import OPTIONS, ConfigOption

IDENTIFIER_HASH_TYPE = "sha256"

def find_project_root(start_path: Optional[Path] = None) -> Path:
    """
    Find the project root by looking for .git directory.

    Args:
        start_path: Starting path for search (default: current working directory)

    Returns:
        Path to project root

    Raises:
        RuntimeError: If project root cannot be found
    """
    current = start_path or Path.cwd()

    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent

    raise RuntimeError("Cannot find project root (no .git directory found)")


class NotInitializedError(Exception):
    """Project not initialized for Zenodo publisher."""
    pass


def load_env(project_root: Path) -> dict[str, str]:
    """
    Load environment variables from .zenodo.env file.

    Args:
        project_root: Path to project root

    Returns:
        Dictionary of environment variables

    Raises:
        NotInitializedError: If .zenodo.env file doesn't exist
    """
    env_file = project_root / ".zenodo.env"

    if not env_file.exists():
        raise NotInitializedError(
            f"Project not initialized for Zenodo publisher.\n"
            f"Missing: {env_file}\n"
        )

    env_vars = {}
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, _, value = line.partition("=")
                env_vars[key.strip()] = value.strip().strip('"').strip("'")

    return env_vars


class Config:
    """Configuration for release tool.

    Options are defined in config_schema.OPTIONS (single source of truth).
    Priority: CLI overrides > .zenodo.env > defaults.
    """

    def __init__(
        self,
        project_root: Path,
        env_vars: dict[str, str],
        cli_overrides: dict[str, Any] | None = None,
    ):
        self.project_root = project_root
        cli_overrides = cli_overrides or {}

        for opt in OPTIONS:
            value = self._resolve_value(opt, env_vars, cli_overrides)
            value = self._coerce(opt, value)

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

        # Validations
        if not self.compile_dir.exists():
            raise FileNotFoundError(
                f"Compile directory not found: {self.compile_dir}\n"
                f"Check COMPILE_DIR in .zenodo.env file"
            )

    def _resolve_value(
        self, opt: ConfigOption, env_vars: dict, cli_overrides: dict
    ) -> Any:
        """Priority: CLI override > env file > default."""
        if opt.name in cli_overrides and cli_overrides[opt.name] is not None:
            return cli_overrides[opt.name]
        if opt.env_key and opt.env_key in env_vars:
            return env_vars[opt.env_key]
        if opt.required and opt.default is None:
            raise ValueError(f"Required config '{opt.env_key}' not set")
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

    def has_zenodo_config(self) -> bool:
        """Check if Zenodo configuration is complete."""
        return self.publisher_type is not None
