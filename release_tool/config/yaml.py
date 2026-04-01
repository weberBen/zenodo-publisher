"""YAML configuration loading for .zp.yaml."""

import yaml
from pathlib import Path
from typing import Any

from .env import ConfigError, NotInitializedError

# Sentinel: la section est valide mais ses sous-clés ne sont pas validées
# (parsées séparément, structure libre)
_OPAQUE = object()

# Sentinel: les clés sont dynamiques (libres), mais leurs valeurs sont validées
# contre le sous-schéma associé
WILDCARD = object()

CONFIG_FILENAME = ".zp.yaml"


def find_config_file(project_root: Path) -> Path | None:
    """Find .zp.yaml in project root."""
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
    
def build_yaml_schema(
    options,
    extra_paths: list[str] | None = None,
    opaque_sections: list[str] | None = None,
) -> dict:
    """Construit l'arbre des clés valides depuis les yaml_path des ConfigOption.

    Retourne un dict imbriqué où :
    - None     = feuille valide
    - _OPAQUE  = section valide dont les sous-clés ne sont pas vérifiées
    - WILDCARD = clé dynamique (segment '*' dans un path) ; la valeur est
                 validée contre le sous-schéma associé
    - dict     = nœud intermédiaire

    Les segments '*' dans les chemins (extra_paths ou opaque_sections) sont
    convertis en WILDCARD dans le schéma, permettant de valider des sections
    à clés dynamiques (e.g. "generated_files.*.sign").
    """
    schema: dict = {}

    def _key(part: str):
        return WILDCARD if part == "*" else part

    all_paths = [opt.yaml_path for opt in options if opt.yaml_path]
    if extra_paths:
        all_paths += extra_paths
    for path in all_paths:
        parts = path.split(".")
        node = schema
        for part in parts[:-1]:
            k = _key(part)
            existing = node.get(k)
            if existing is None or existing is _OPAQUE:
                node[k] = {}
            node = node[k]
        node[_key(parts[-1])] = None

    for section in (opaque_sections or []):
        parts = section.split(".")
        node = schema
        for part in parts[:-1]:
            k = _key(part)
            if node.get(k) is None or node.get(k) is _OPAQUE:
                node[k] = {}
            node = node[k]
        node[_key(parts[-1])] = _OPAQUE

    return schema


def validate_yaml_unknown_keys(
    yaml_config: dict,
    schema: dict,
    _prefix: str = "",
) -> None:
    """Lève ConfigError si une clé (ou sous-clé) YAML n'existe pas dans le schéma."""
    for key, value in yaml_config.items():
        full_path = f"{_prefix}.{key}" if _prefix else key
        if key in schema:
            sub_schema = schema[key]
        elif WILDCARD in schema:
            sub_schema = schema[WILDCARD]
        else:
            raise ConfigError(
                f"Unknown config key: '{full_path}'",
                name="yaml.unknown_key",
            )
        if sub_schema is _OPAQUE:
            continue
        if sub_schema is not None and isinstance(value, dict):
            validate_yaml_unknown_keys(value, sub_schema, _prefix=full_path)


def traverse_yaml(config: dict, path: str) -> Any:
    """Traverse nested dict by dot-separated path. Returns None if missing."""
    keys = path.split(".")
    current = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current
