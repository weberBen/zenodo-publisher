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


def load_yaml(project_root: Path) -> dict:
    """Load and parse zenodo_config.yaml."""
    path = find_config_file(project_root)
    if path is None:
        raise NotInitializedError(
            f"Missing config file: {project_root / CONFIG_FILENAME}"
        )
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(f"{CONFIG_FILENAME} must be a YAML mapping")
    return data


def traverse_yaml(config: dict, path: str) -> Any:
    """Traverse nested dict by dot-separated path. Returns None if missing."""
    keys = path.split(".")
    current = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current
