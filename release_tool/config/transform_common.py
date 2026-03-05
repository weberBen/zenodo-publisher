"""Common config transforms and constants."""

import re
from pathlib import Path

import hashlib

from .schema import dedup_args
from .env import InvalidValueError

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
# Template variables allowed in generated_files pattern paths
PATTERN_TEMPLATE_VARS = ["compile_dir", "project_root", "project_name"]
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
            f"'.' deliminator are not allowed"
        )
    
    found_vars = _TEMPLATE_VAR_RE.findall(value)
    if not found_vars:
        return
    invalid = [v for v in found_vars if v not in PROJECT_NAME_TEMPLATE_VARS]
    if invalid:
        valid = ", ".join(PROJECT_NAME_TEMPLATE_VARS)
        raise InvalidValueError(
            f"Unknown template variable(s): "
            f"{', '.join(invalid)}. Allowed variables: {valid}"
        )

def _validate_pattern_template(value):
    """Check that all {var} placeholders in a pattern path are allowed."""
    if not value or not isinstance(value, str):
        return
    found_vars = _TEMPLATE_VAR_RE.findall(value)
    if not found_vars:
        return
    invalid = [v for v in found_vars if v not in PATTERN_TEMPLATE_VARS]
    if invalid:
        valid = ", ".join(PATTERN_TEMPLATE_VARS)
        raise InvalidValueError(
            f"Unknown template variable(s) in pattern: "
            f"{', '.join(invalid)}. Allowed: {valid}"
        )


def is_iterable_of_strings(obj):
    try:
        return all(isinstance(item, str) for item in obj)
    except TypeError:
        return False
   
def _validate_hash_algorithm(value) -> bool:
    """Check if an algorithm is supported (hashlib or tree alias)."""
    if value in TREE_ALGORITHMS:
        return True
    try:
        hashlib.new(value)
        return True
    except ValueError:
        return False

def validate_hash_algorithms(value) -> bool:
    """Check that all configured hash algorithms are supported."""
    if type(value) is str:
        value = [value]
    if not is_iterable_of_strings(value):
        raise InvalidValueError("not a list of hash algo names")
    
    invalid = [a for a in value if not _validate_hash_algorithm(a)]
    if invalid:
        raise InvalidValueError(
            f"Unsupported hash algorithms: {', '.join(invalid)}"
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
            "Custom tar args detected. This may affect archive reproducibility",
            stacklevel=2,
        )
    return result


def _build_gzip_args(value, project_root):
    """Merge default GZIP args with user args."""
    result = dedup_args(GZIP_DEFAULT_ARGS, value or [])
    if result != GZIP_DEFAULT_ARGS:
        import warnings
        warnings.warn(
            "Custom gzip args detected. This may affect archive reproducibility",
            stacklevel=2,
        )
    return result
