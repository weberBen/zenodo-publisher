"""Environment loading, validation, and config exceptions."""

from pathlib import Path
from typing import Any, Optional

from .schema import ConfigOption
from ..errors import ZPError


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConfigError(ZPError):
    """Base error for configuration problems."""
    _prefix = "config"


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
# Sensitive env keys (kept in .zenodo.env or actual environment)
# ---------------------------------------------------------------------------

SENSITIVE_ENV_KEYS: set[str] = {"ZENODO_TOKEN", "ZENODO_CONCEPT_DOI"}


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
            f"Missing: {env_file}\n",
            name="not_initialized",
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
            f"Unknown keys in .zenodo.env: {', '.join(sorted(unknown))}",
            name="unknown_env_key",
        )


def validate_type(opt: ConfigOption, value: Any) -> None:
    pass


def validate_choices(opt: ConfigOption, value: Any) -> None:
    """Check that value is in opt.choices if defined."""
    if value is None or opt.choices is None:
        return
    if isinstance(value, list):
        invalid = [v for v in value if v not in opt.choices]
        if invalid:
            raise InvalidValueError(
                f"contains invalid values: "
                f"{', '.join(invalid)}. Must be one of {opt.choices}",
                name="invalid_value",
            )
        return
    if value not in opt.choices:
        raise InvalidValueError(
            f"must be one of {opt.choices}, got '{value}'",
            name="invalid_value",
        )
