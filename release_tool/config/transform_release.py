"""Release-specific config transforms and constants."""

from .schema import dedup_args
from .env import InvalidValueError

_MAKE_DEFAULT_ARGS = []

COMMIT_FIELD_MAP = {
    "sha": "ZP_COMMIT_SHA",
    "date_epoch": "ZP_COMMIT_DATE_EPOCH",
    "subject": "ZP_COMMIT_SUBJECT",
    "author_name": "ZP_COMMIT_AUTHOR_NAME",
    "author_email": "ZP_COMMIT_AUTHOR_EMAIL",
    "branch": "ZP_BRANCH",
    "origin": "ZP_ORIGIN_URL",
    "tag_sha": "ZP_TAG_SHA",
}


def _resolve_compile_dir(value, project_root):
    """Resolve COMPILE_DIR relative to project_root."""
    if not project_root:
        return None
    return project_root / value


def _dedup_make_args(value, project_root):
    """Dedup make args."""
    return dedup_args(_MAKE_DEFAULT_ARGS, value or [])


def _validate_commit_fields(value):
    """Check that all items are valid COMMIT_FIELD_MAP keys."""
    if not value:
        return
    invalid = [f for f in value if f not in COMMIT_FIELD_MAP]
    if invalid:
        valid = ", ".join(sorted(COMMIT_FIELD_MAP))
        raise InvalidValueError(
            f"Unknown commit fields: {', '.join(invalid)}. "
            f"Valid fields: {valid}"
        )
