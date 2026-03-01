"""Configuration schema: ConfigOption dataclass and generic utilities."""

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ConfigOption:
    """Describes a single configuration option.

    Used to auto-generate both config loading from .zenodo.env
    and CLI arguments from argparse.
    """
    name: str                  # Config attribute name: "gpg_sign"
    env_key: str | None        # Env var key: "GPG_SIGN" (None = not in .zenodo.env)
    type: str = "str"          # "str", "bool", "optional_str", "list", "store_true"
    default: Any = None
    cli: bool = True           # False to hide from CLI (e.g. ZENODO_TOKEN)
    help: str = ""
    transform: Callable | None = None   # (value, project_root) -> value
    extra_attrs: list[str] = field(default_factory=list)
    choices: list[str] | None = None


def dedup_args(default_args: list[str], user_args: list[str]) -> list[str]:
    """Merge default and user args, last value wins for same key.

    --no-X in user_args removes --X from defaults (not passed to subprocess).

    Handles: --flag/--no-flag, --key=value, -Xvalue, KEY=value.
    """
    def _arg_key(arg):
        if arg.startswith("--"):
            return arg.split("=")[0][2:]   # --armor → armor, --key=val → key
        if arg.startswith("-") and len(arg) > 2:
            return arg[:2]                 # -j4 → -j
        if "=" in arg:
            return arg.split("=")[0]       # VERBOSE=1 → VERBOSE
        return arg

    seen = {}
    order = []
    for arg in default_args:
        key = _arg_key(arg)
        if key not in seen:
            order.append(key)
        seen[key] = arg
    for arg in user_args:
        if arg.startswith("--no-"):
            # --no-X in user_args removes --X from defaults
            key = arg[5:]
            if key in seen:
                order.remove(key)
                del seen[key]
            continue
        key = _arg_key(arg)
        if key not in seen:
            order.append(key)
        seen[key] = arg
    return [seen[k] for k in order]
