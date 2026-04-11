"""Pipeline checkpoint: serialize/restore PipelineContext using dill.

The checkpoint is a binary .pkl file containing:
    {
        "version": <app version string>,
        "last_completed_step": <HookPoint.value string>,
        "ctx": <PipelineContext instance>,
    }

Loading is refused if the stored version doesn't match the current app version,
preventing partial restores across incompatible releases.
"""

import shutil
import dill
from importlib.metadata import version as _pkg_version
from pathlib import Path

from .. import output
from .context import HookPoint, PipelineContext

_ZP_CACHE_BASE = Path(".zp") / "cache"

CHECKPOINT_FILE = ".zp_checkpoint.pkl"

try:
    _APP_VERSION = _pkg_version("zenodo-publisher")
except Exception:
    _APP_VERSION = "unknown"

def get_cache_dir(project_root: Path, cache_id: str) -> Path:
    """Return the canonical cache dir path for a given tag."""
    return project_root / _ZP_CACHE_BASE / cache_id


def does_cache_exists(project_root: Path, cache_id: str) -> bool:
    cache_dir = get_cache_dir(project_root, cache_id)
    return cache_dir.exists()

def delete_cache_dir(cache_id: str, project_root: Path) -> None:
    """Delete cache_dir, after verifying it sits inside project_root/.zp/cache/.

    Raises ValueError if cache_dir is not within the expected base to prevent
    accidentally deleting an unrelated directory.
    """
    cache_dir = project_root / _ZP_CACHE_BASE / cache_id
    shutil.rmtree(cache_dir, ignore_errors=True)


def write_checkpoint(ctx: PipelineContext, cache_id: str, last_completed: HookPoint) -> None:
    """Serialize ctx to cache_dir/.zp_checkpoint.pkl."""
    cache_dir = get_cache_dir(ctx.config.project_root, cache_id)
    data = {
        "version": _APP_VERSION,
        "last_completed_step": last_completed.value,
        "ctx": ctx,
    }
    with open(cache_dir / CHECKPOINT_FILE, "wb") as f:
        dill.dump(data, f)


def read_checkpoint(cache_id: str, project_root: Path) -> dict | None:
    """Load checkpoint from cache_dir. Returns None if absent or version mismatch."""
    p = get_cache_dir(project_root, cache_id) / CHECKPOINT_FILE
    if not p.exists():
        return None
    with open(p, "rb") as f:
        data = dill.load(f)
    stored = data.get("version", "unknown")
    if stored != _APP_VERSION:
        output.warn(
            "Checkpoint version {old} != app version {new} — ignoring cache",
            old=stored, new=_APP_VERSION,
            name="cache.version_mismatch",
        )
        return None
    return data


def restore_from_checkpoint(ctx: PipelineContext, checkpoint: dict) -> HookPoint:
    """Copy saved state into ctx (including config). Returns the last completed HookPoint."""
    saved: PipelineContext = checkpoint["ctx"]
    ctx.config = saved.config
    ctx.tag_name = saved.tag_name
    ctx.commit_env = saved.commit_env
    ctx.record_info = saved.record_info
    ctx.archived_files = saved.archived_files
    return HookPoint(checkpoint["last_completed_step"])
