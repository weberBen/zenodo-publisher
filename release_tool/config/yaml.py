"""YAML configuration loading for zenodo_config.yaml."""

import yaml
from pathlib import Path
from typing import Any

from .env import ConfigError, NotInitializedError

CONFIG_FILENAME = "zenodo_config.yaml"


def find_config_file(project_root: Path) -> Path | None:
    """Find zenodo_config.yaml in project root."""
    path = project_root / CONFIG_FILENAME
    return path if path.exists() else None

def _load_yaml_file(path: str | Path) -> dict:
    """Load and parse a YAML config file from an explicit path."""
    if not path:
        raise ConfigError("No config file path provided", name="yaml.no_path")
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}", name="yaml.not_found")
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must be a YAML mapping", name="yaml.invalid_format")
    return data

def load_yaml_file(path: str | Path, raise_exception=True) -> dict | None:
    """Load and parse a YAML config file from an explicit path."""
    try:
        return _load_yaml_file(path)
    except ConfigError:
        if raise_exception:
            raise
        return None
    
def traverse_yaml(config: dict, path: str) -> Any:
    """Traverse nested dict by dot-separated path. Returns None if missing."""
    keys = path.split(".")
    current = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current
