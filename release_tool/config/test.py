"""Test configuration dataclass and loader."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .env import ConfigError


@dataclass
class TestConfig:
    """Test mode configuration, loaded from an external YAML file."""
    mode: bool = True
    prompts: dict[str, str] = field(default_factory=dict)
    cli: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_args(cls, args) -> "TestConfig | None":
        """Build TestConfig from CLI args. Returns None if test mode is off."""
        test_config_path = getattr(args, "test_config", None)
        if test_config_path:
            return _load_test_config_file(Path(test_config_path))
        if getattr(args, "test_mode", False):
            return cls()
        return None


def parse_test_config(raw: Any) -> TestConfig | None:
    """Parse a raw dict into TestConfig.

    Returns None if the input is absent or empty.
    """
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("test config must be a YAML mapping")

    cfg = TestConfig()

    if "mode" in raw:
        cfg.mode = bool(raw["mode"])

    if "prompts" in raw:
        prompts = raw["prompts"]
        if not isinstance(prompts, dict):
            raise ConfigError("'prompts' must be a YAML mapping")
        cfg.prompts = {str(k): str(v) if v is not None else "" for k, v in prompts.items()}

    if "cli" in raw:
        cli = raw["cli"]
        if not isinstance(cli, dict):
            raise ConfigError("'cli' must be a YAML mapping")
        cfg.cli = cli

    return cfg


def _load_test_config_file(path: Path) -> TestConfig:
    """Load a test config from a YAML file path."""
    if not path.exists():
        raise ConfigError(f"Test config file not found: {path}")
    with open(path) as f:
        data = yaml.safe_load(f)
    cfg = parse_test_config(data)
    if cfg is None:
        raise ConfigError(f"Test config file is empty: {path}")
    return cfg
