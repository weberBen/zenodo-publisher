"""Release-specific config transforms and constants."""

from .config_schema import dedup_args

_GPG_DEFAULT_ARGS = ["--armor"]
_MAKE_DEFAULT_ARGS = []

def _parse_main_file(value, project_root):
    """Split 'main.pdf' into ('main', 'pdf')."""
    parts = value.split(".")
    return parts[0], ".".join(parts[1:])


def _resolve_compile_dir(value, project_root):
    """Resolve COMPILE_DIR relative to project_root."""
    if not project_root:
        return None
    return project_root / value


def _strip_or_none(value, project_root):
    """Strip whitespace, return None if empty."""
    v = value.strip() if value else ""
    return v if v else None


def _build_gpg_args(value, project_root):
    """Merge default GPG args with user args."""
    return dedup_args(_GPG_DEFAULT_ARGS, value or [])


def _dedup_make_args(value, project_root):
    """Dedup make args."""
    return dedup_args(_MAKE_DEFAULT_ARGS, value or [])
