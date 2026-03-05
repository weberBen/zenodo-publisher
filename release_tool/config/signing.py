"""Signing configuration dataclass and YAML parser."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import hashlib

from .env import ConfigError
from .schema import dedup_args


class SignMode(Enum):
    FILE = "file"
    FILE_HASH = "file_hash"


GPG_DEFAULT_ARGS = ["--armor"]


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
            f"Invalid sign_mode '{value}'. Valid: {', '.join(valid)}"
        )


def _validate_hash_algo(algo: str) -> None:
    if algo not in hashlib.algorithms_available:
        raise ConfigError(f"Unknown hash algorithm '{algo}'")


def parse_signing_config(raw: Any) -> SigningConfig:
    """Parse the 'signing' section from YAML into SigningConfig."""
    if not raw:
        return SigningConfig()
    if not isinstance(raw, dict):
        raise ConfigError("'signing' must be a YAML mapping")

    cfg = SigningConfig()

    if "sign" in raw:
        cfg.sign = bool(raw["sign"])

    if "sign_mode" in raw:
        cfg.sign_mode = _parse_sign_mode(raw["sign_mode"])

    if "sign_hash_algo" in raw:
        algo = str(raw["sign_hash_algo"])
        _validate_hash_algo(algo)
        cfg.sign_hash_algo = algo

    gpg = raw.get("gpg", {})
    if isinstance(gpg, dict):
        if "uid" in gpg:
            uid = gpg["uid"]
            cfg.gpg_uid = str(uid).strip() if uid else None
        if "extra_args" in gpg:
            user_args = gpg["extra_args"]
            if isinstance(user_args, list):
                cfg.gpg_extra_args = dedup_args(GPG_DEFAULT_ARGS, user_args)
            elif isinstance(user_args, str):
                parts = [a.strip() for a in user_args.split(",") if a.strip()]
                cfg.gpg_extra_args = dedup_args(GPG_DEFAULT_ARGS, parts)

    return cfg
