"""Signing configuration dataclass and YAML parser."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import hashlib

from .env import ConfigError
from .schema import ConfigOption, dedup_args


class SignMode(Enum):
    FILE = "file"
    FILE_HASH = "file_hash"


GPG_DEFAULT_ARGS = ["--armor"]


SIGNING_OPTIONS: list[ConfigOption] = [
    ConfigOption("sign", env_key=None,
                 yaml_path="signing.sign",
                 type="bool", default=False,
                 help="Enable GPG signing"),
    ConfigOption("sign_mode", env_key=None,
                 yaml_path="signing.sign_mode",
                 default=SignMode.FILE_HASH.value,
                 choices=[m.value for m in SignMode],
                 help="Signing mode: file or file_hash"),
    ConfigOption("sign_hash_algo", env_key=None,
                 yaml_path="signing.sign_hash_algo",
                 default="sha256",
                 help="Hash algorithm for signing"),
    ConfigOption("gpg_uid", env_key=None,
                 yaml_path="signing.gpg.uid",
                 nullable=True,
                 help="GPG user ID"),
]


@dataclass
class SigningConfig:
    """Global signing configuration with defaults."""
    sign: bool = False
    sign_mode: SignMode = SignMode.FILE_HASH
    sign_hash_algo: str = "sha256"
    gpg_uid: str | None = None
    gpg_extra_args: list[str] = field(default_factory=lambda: list(GPG_DEFAULT_ARGS))


def _parse_sign_mode(value: str) -> SignMode:
    try:
        return SignMode(value)
    except ValueError:
        valid = [m.value for m in SignMode]
        raise ConfigError(
            f"Invalid sign_mode '{value}'. Valid: {', '.join(valid)}",
            name="signing.invalid_mode",
        )


def _validate_hash_algo(algo: str) -> None:
    if algo not in hashlib.algorithms_available:
        raise ConfigError(f"Unknown hash algorithm '{algo}'", name="signing.algo.unknown")


def parse_signing_config(raw: Any, cli_overrides: dict | None = None) -> SigningConfig:
    """Parse the 'signing' section from YAML into SigningConfig.

    Priority: cli_overrides > YAML > dataclass defaults.
    """
    if not raw:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("'signing' must be a YAML mapping", name="signing.invalid_format")

    cli = cli_overrides or {}
    cfg = SigningConfig()

    # sign
    if "sign" in cli and cli["sign"] is not None:
        cfg.sign = cli["sign"]
    elif "sign" in raw:
        cfg.sign = raw["sign"]

    # sign_mode
    if "sign_mode" in cli and cli["sign_mode"] is not None:
        cfg.sign_mode = _parse_sign_mode(cli["sign_mode"])
    elif "sign_mode" in raw:
        cfg.sign_mode = _parse_sign_mode(raw["sign_mode"])

    # sign_hash_algo
    if "sign_hash_algo" in cli and cli["sign_hash_algo"] is not None:
        algo = str(cli["sign_hash_algo"])
        _validate_hash_algo(algo)
        cfg.sign_hash_algo = algo
    elif "sign_hash_algo" in raw:
        algo = str(raw["sign_hash_algo"])
        _validate_hash_algo(algo)
        cfg.sign_hash_algo = algo

    # gpg_uid
    if "gpg_uid" in cli and cli["gpg_uid"] is not None:
        cfg.gpg_uid = str(cli["gpg_uid"]).strip()
    else:
        gpg = raw.get("gpg", {})
        if isinstance(gpg, dict) and "uid" in gpg:
            uid = gpg["uid"]
            cfg.gpg_uid = str(uid).strip() if uid else None

    # gpg_extra_args (YAML only)
    gpg = raw.get("gpg", {})
    if isinstance(gpg, dict) and "extra_args" in gpg:
        user_args = gpg["extra_args"]
        if isinstance(user_args, list):
            cfg.gpg_extra_args = dedup_args(GPG_DEFAULT_ARGS, user_args)
        elif isinstance(user_args, str):
            parts = [a.strip() for a in user_args.split(",") if a.strip()]
            cfg.gpg_extra_args = dedup_args(GPG_DEFAULT_ARGS, parts)

    return cfg
