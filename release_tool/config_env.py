"""Environment loading, validation, and config exceptions."""

from pathlib import Path
from typing import Any, Optional

from .config_schema import ConfigOption


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """Base error for configuration problems."""
    pass


class NotInitializedError(ConfigError):
    """Project not initialized for Zenodo publisher."""
    pass


class UnknownEnvKeyError(ConfigError):
    """Unknown key found in .zenodo.env."""
    pass


class InvalidValueError(ConfigError):
    """Invalid value for a configuration option."""
    pass


# ---------------------------------------------------------------------------
# Project root / env file helpers
# ---------------------------------------------------------------------------

def find_project_root(start_path: Optional[Path] = None) -> Path:
    """Find the project root by looking for .git directory.

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


def load_env(project_root: Path) -> dict[str, str]:
    """Load environment variables from .zenodo.env file.

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


# ---------------------------------------------------------------------------
# Validation functions
# ---------------------------------------------------------------------------

def validate_env_keys(env_vars: dict[str, str], all_env_keys: set[str]) -> None:
    """Raise if env_vars contains keys not in any config option."""
    unknown = set(env_vars.keys()) - all_env_keys
    if unknown:
        raise UnknownEnvKeyError(
            f"Unknown keys in .zenodo.env: {', '.join(sorted(unknown))}"
        )


def validate_type(opt: ConfigOption, value: Any) -> None:
    """Check that bool values are 'true' or 'false' strings."""
    if value is None:
        return
    if opt.type == "bool" and isinstance(value, str):
        if value.lower() not in ("true", "false"):
            raise InvalidValueError(
                f"'{opt.env_key or opt.name}' must be 'true' or 'false', got '{value}'"
            )


def validate_choices(opt: ConfigOption, value: Any) -> None:
    """Check that value is in opt.choices if defined."""
    if value is None or opt.choices is None:
        return
    if value not in opt.choices:
        raise InvalidValueError(
            f"'{opt.env_key or opt.name}' must be one of {opt.choices}, got '{value}'"
        )
