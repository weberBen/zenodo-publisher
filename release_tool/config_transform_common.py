"""Common config transforms and constants."""

import re
from pathlib import Path

from .config_schema import dedup_args
from .config_env import InvalidValueError

# for reproductibility
TAR_DEFAULT_ARGS = [
    "--sort=name", "--format=posix",
    "--pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime",
    "--mtime=1970-01-01 00:00:00Z",
    "--numeric-owner", "--owner=0", "--group=0",
    "--mode=go+u,go-w",
]
GZIP_DEFAULT_ARGS = ["--no-name", "--best"]

TREE_ALGORITHMS = {"tree": "sha1", "tree256": "sha256"}

# Template variables allowed in project_name_suffix
PROJECT_NAME_TEMPLATE_VARS = ["tag_name", "sha_commit"]
_TEMPLATE_VAR_RE = re.compile(r"\{(\w+)\}")


def _resolve_project_name_prefix(value, project_root):
    """Return value if non-empty, otherwise project root directory name."""
    v = value.strip() if value else ""
    if v:
        return v
    return project_root.name if project_root else None


def _validate_project_name_suffix(value):
    """Check that all {var} placeholders in suffix are allowed."""
    if not value or not isinstance(value, str):
        return
    if "." in value:
        raise InvalidValueError(
            f"'.' deliminator are not allowed in project name suffix"
        )
    
    found_vars = _TEMPLATE_VAR_RE.findall(value)
    if not found_vars:
        return
    invalid = [v for v in found_vars if v not in PROJECT_NAME_TEMPLATE_VARS]
    if invalid:
        valid = ", ".join(PROJECT_NAME_TEMPLATE_VARS)
        raise InvalidValueError(
            f"Unknown template variable(s) in PROJECT_NAME_SUFFIX: "
            f"{', '.join(invalid)}. Allowed variables: {valid}"
        )


def _resolve_optional_path(value, project_root):
    """Parse optional path, return None if empty."""
    return Path(value) if value else None


def _build_tar_args(value, project_root):
    """Merge default TAR args with user args."""
    result = dedup_args(TAR_DEFAULT_ARGS, value or [])
    if result != TAR_DEFAULT_ARGS:
        import warnings
        warnings.warn(
            "Custom tar args detected — this may affect archive reproducibility",
            stacklevel=2,
        )
    return result


def _build_gzip_args(value, project_root):
    """Merge default GZIP args with user args."""
    result = dedup_args(GZIP_DEFAULT_ARGS, value or [])
    if result != GZIP_DEFAULT_ARGS:
        import warnings
        warnings.warn(
            "Custom gzip args detected — this may affect archive reproducibility",
            stacklevel=2,
        )
    return result
