"""Async job manager for zenodo-publisher.

Modules can schedule deferred tasks (e.g. OTS proof upgrade) that ZP stores
as JSON files in ~/.zp/jobs/. The ``zp jobs`` command processes them later.

Job lifecycle:
  1. Module returns a ``job`` descriptor in its ``run`` result
  2. ZP enriches it with project context and writes to ~/.zp/jobs/
  3. ``zp jobs run`` processes eligible jobs (retry interval elapsed)
  4. Changed files are synced back to the project archive dir
"""

import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from . import output
from .modules import find_module_path, run_module_job

DEFAULT_RETRY_MAX = 100

# ---------------------------------------------------------------------------
# Retry interval parsing
# ---------------------------------------------------------------------------

def _parse_interval(raw: str | int | float) -> int:
    """Parse a human interval like '30m', '1h', '5min' into seconds."""
    if isinstance(raw, (int, float)):
        return int(raw)
    raw = raw.strip().lower()
    multipliers = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600}
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if raw.endswith(suffix):
            return int(float(raw[:-len(suffix)]) * mult)
    return int(raw)


# ---------------------------------------------------------------------------
# Job ID + paths
# ---------------------------------------------------------------------------

def _get_jobs_dir() -> Path:
    """Return the jobs directory path (overridable via ZP_JOBS_DIR env var)."""
    custom = os.environ.get("ZP_JOBS_DIR")
    if custom:
        return Path(custom)
    return Path.home() / ".zp" / "jobs"


def _jobs_dir() -> Path:
    d = _get_jobs_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_job_id() -> str:
    import uuid
    return uuid.uuid4().hex[:10]


def _job_path(job_id: str) -> Path:
    return _jobs_dir() / f"{job_id}.json"


# ---------------------------------------------------------------------------
# Job CRUD
# ---------------------------------------------------------------------------

def create_job(
    module_name: str,
    job_descriptor: dict,
    tag_name: str,
    project_root: Path,
    archive_dir: Path | None,
    config: dict,
    files: list[dict],
) -> Path:
    """Write a job JSON file to ~/.zp/jobs/. Returns the path.

    Root-level fields are ZP-controlled. Module-provided data (job_descriptor
    and per-file module_config) is stored under "input".
    """
    # Safe-parse module-provided fields
    retry_interval = _parse_interval(job_descriptor.get("retry_interval", 1800))
    raw_max = job_descriptor.get("retry_max", DEFAULT_RETRY_MAX)
    retry_max = None if raw_max is None else int(raw_max)
    description = str(job_descriptor.get("description", ""))

    # Extract module_config from files into input.files keyed by config_key
    input_files = {}
    clean_files = []
    for f in files:
        fc = dict(f)
        mc = fc.pop("module_config", None)
        if mc:
            input_files[fc["config_key"]] = mc
        clean_files.append(fc)

    job_id = _make_job_id()
    job = {
        "id": job_id,
        "module_name": module_name,
        "tag_name": tag_name,
        "project_root": str(project_root),
        "archive_dir": str(archive_dir) if archive_dir else None,
        "created_at": time.time(),
        "status": "pending",
        "retry_interval_seconds": retry_interval,
        "retry_count": 0,
        "retry_max": retry_max,
        "description": description,
        "last_attempt_at": None,
        "errors": [],
        "config": config,
        "files": clean_files,
        "input": {
            "job_descriptor": job_descriptor,
            "files": input_files,
        },
    }
    path = _job_path(job_id)
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    return path


def _load_job(path: Path) -> dict | None:
    """Load a job from path, injecting _path and backfilling id if missing."""
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
        job["_path"] = str(path)
        if "id" not in job:
            job["id"] = path.stem
        return job
    except (json.JSONDecodeError, KeyError):
        return None


def list_jobs(*, status_filter: str | None = None) -> list[dict]:
    """Return all jobs, optionally filtered by status."""
    result = []
    for p in sorted(_jobs_dir().glob("*.json")):
        job = _load_job(p)
        if job is None:
            continue
        if status_filter and job.get("status") != status_filter:
            continue
        result.append(job)
    return result


def get_job(job_id: str) -> dict | None:
    """Load a single job by ID."""
    path = _job_path(job_id)
    if path.exists():
        return _load_job(path)
    # Fallback: scan for partial match (prefix)
    for p in _jobs_dir().glob("*.json"):
        if p.stem.startswith(job_id):
            return _load_job(p)
    return None


def remove_job(job_id: str) -> bool:
    """Remove a job file by ID. Returns True if removed."""
    job = get_job(job_id)
    if job is None:
        return False
    Path(job["_path"]).unlink(missing_ok=True)
    return True


def count_pending() -> int:
    """Count pending jobs (fast, no full parse)."""
    jobs_dir = _get_jobs_dir()
    if not jobs_dir.exists():
        return 0
    count = 0
    for p in jobs_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("status") == "pending":
                count += 1
        except (json.JSONDecodeError, KeyError):
            continue
    return count


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

def _is_retry_exhausted(job: dict) -> bool:
    """Check if the job has exceeded its maximum retry count."""
    retry_max = job.get("retry_max")
    if retry_max is None:
        return False
    return job.get("retry_count", 0) >= retry_max


def _is_eligible(job: dict) -> bool:
    """Check if a pending job is eligible for retry (interval elapsed, retries not exhausted)."""
    if job.get("status") != "pending":
        return False
    if _is_retry_exhausted(job):
        return False
    last = job.get("last_attempt_at")
    if last is None:
        return True
    interval = job.get("retry_interval_seconds", 1800)
    return time.time() >= last + interval


def _time_until_eligible(job: dict) -> int | str | None:
    """Seconds until job becomes eligible. None if already eligible.
    Returns "max_retries" string if retry limit reached.
    """
    if _is_retry_exhausted(job):
        return "max_retries"
    last = job.get("last_attempt_at")
    if last is None:
        return None
    interval = job.get("retry_interval_seconds", 1800)
    remaining = (last + interval) - time.time()
    return max(0, int(remaining)) if remaining > 0 else None


def _format_duration(seconds: int) -> str:
    if seconds >= 3600:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


# ---------------------------------------------------------------------------
# File integrity
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_jobs_table(jobs: list[dict]) -> None:
    """Print a summary table of jobs with dynamic column widths."""
    if not jobs:
        output.info("No jobs found.", name="jobs.empty")
        return

    # Build row data first so we can compute column widths
    columns = ["ID", "MODULE", "TAG", "STATUS", "RETRIES", "NEXT IN", "DESCRIPTION"]
    rows = []
    for job in jobs:
        remaining = _time_until_eligible(job)
        if job["status"] != "pending":
            next_in = job["status"]
        elif remaining == "max_retries":
            next_in = "exhausted"
        elif remaining is None or remaining == 0:
            next_in = "ready"
        else:
            next_in = _format_duration(remaining)

        rows.append([
            job["id"],
            job["module_name"],
            job["tag_name"],
            job["status"],
            str(job["retry_count"]),
            next_in,
            job.get("description", ""),
        ])

    # Compute widths: max of header and all row values, +2 padding
    widths = [max(len(columns[i]), *(len(r[i]) for r in rows)) + 2
              for i in range(len(columns))]

    def _fmt_row(values):
        return "".join(v.ljust(w) for v, w in zip(values, widths))

    output.info(_fmt_row(columns), name="jobs.header")
    output.info("-" * sum(widths), name="jobs.separator")
    for row in rows:
        output.info(_fmt_row(row), name="jobs.row")

    if any(j.get("errors") for j in jobs):
        output.info("", name="jobs.blank")
        for job in jobs:
            for err in (job.get("errors") or [])[-3:]:
                output.warn(
                    "[{id}] {error}",
                    id=job["id"], error=err, name="jobs.error",
                )


def print_job_info(job: dict) -> None:
    """Print detailed info for a single job."""
    output.info("Job: {id}", id=job["id"], name="jobs.info.id")
    output.info("  Module:      {v}", v=job["module_name"], name="jobs.info.module")
    output.info("  Tag:         {v}", v=job["tag_name"], name="jobs.info.tag")
    output.info("  Status:      {v}", v=job["status"], name="jobs.info.status")
    output.info("  Description: {v}", v=job.get("description", ""), name="jobs.info.description")
    output.info("  Project:     {v}", v=job.get("project_root", ""), name="jobs.info.project")
    output.info("  Archive dir: {v}", v=job.get("archive_dir", ""), name="jobs.info.archive_dir")

    created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(job.get("created_at", 0)))
    output.info("  Created:     {v}", v=created, name="jobs.info.created")

    retry_max = job.get("retry_max")
    max_str = "unlimited" if retry_max is None else str(retry_max)
    interval = job.get("retry_interval_seconds", 0)
    output.info("  Retries:     {count} / {max} (interval: {interval})",
                count=job.get("retry_count", 0), max=max_str,
                interval=_format_duration(interval), name="jobs.info.retries")

    last = job.get("last_attempt_at")
    if last:
        last_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last))
        output.info("  Last attempt: {v}", v=last_str, name="jobs.info.last_attempt")

    files = job.get("files", [])
    output.info("  Files ({n}):", n=len(files), name="jobs.info.files_header")
    for f in files:
        rel = f.get("relative_path", f.get("file_path", "?"))
        output.info("    - {path}", path=rel, name="jobs.info.file")

    errors = job.get("errors", [])
    if errors:
        output.info("  Errors ({n}):", n=len(errors), name="jobs.info.errors_header")
        for err in errors[-5:]:
            output.warn("    {error}", error=err, name="jobs.info.error")


# ---------------------------------------------------------------------------
# Job runner
# ---------------------------------------------------------------------------

def _save_job(job: dict) -> None:
    """Write job dict back to its file."""
    path = Path(job["_path"])
    data = {k: v for k, v in job.items() if k != "_path"}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _copy_files_to_workdir(job: dict, workdir: Path) -> dict[str, str]:
    """Copy job files from archive to workdir. Returns {relative_path: identifier}."""
    archive_dir = job.get("archive_dir")
    tag = job["tag_name"]
    identifiers = {}

    if not archive_dir:
        return identifiers

    archive_tag_dir = Path(archive_dir) / tag

    for f in job.get("files", []):
        rel = f["relative_path"]
        src = archive_tag_dir / rel
        if not src.exists():
            output.warn(
                "File not found in archive: {path}",
                path=str(src), name="jobs.file_missing",
            )
            continue
        dst = workdir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        actual_id = _sha256(dst)
        expected_id = f.get("identifier")
        if expected_id and actual_id != expected_id:
            output.warn(
                "Integrity mismatch for {path}: expected {expected}, got {actual}",
                path=rel, expected=expected_id[:16], actual=actual_id[:16],
                name="jobs.integrity_mismatch",
            )
        identifiers[rel] = actual_id

    return identifiers


def _sync_back_to_archive(job: dict, workdir: Path, before_ids: dict[str, str]) -> None:
    """Scan the module subdirectory in workdir and sync back to archive.

    Only scans workdir/{module_name}/ — the module's output area.
    For each file:
      - New (not in archive): copy
      - Unchanged (same SHA256): skip
      - Modified (different SHA256): prompt overwrite/backup/skip
    """
    archive_dir = job.get("archive_dir")
    tag = job["tag_name"]
    module_name = job["module_name"]

    if not archive_dir:
        return

    module_dir = workdir / module_name
    if not module_dir.exists():
        return

    archive_tag_dir = Path(archive_dir) / tag

    from . import prompts

    for child in module_dir.rglob("*"):
        if not child.is_file():
            continue
        rel = str(child.relative_to(workdir))
        dst = archive_tag_dir / rel
        new_id = _sha256(child)

        if not dst.exists():
            # New file — just copy
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(child), str(dst))
            output.detail_ok("New file archived: {path}", path=rel, name="jobs.new_archived")
            continue

        # Existing file — check if modified
        old_id = before_ids.get(rel) or _sha256(dst)
        if new_id == old_id:
            continue  # unchanged

        output.info("File changed: {path}", path=rel, name="jobs.file_changed")
        result = prompts.confirm_job_overwrite.ask(
            f"Overwrite {rel}? (y/n/backup)"
        )
        if result.value == "backup":
            bak = dst.with_suffix(dst.suffix + ".backup")
            shutil.copy2(str(dst), str(bak))
            output.detail("Backup: {path}", path=str(bak), name="jobs.backup")
        elif not result.is_accept:
            output.detail("Skipped: {path}", path=rel, name="jobs.skip")
            continue

        shutil.copy2(str(child), str(dst))
        output.detail_ok("Archived: {path}", path=rel, name="jobs.archived")



def run_single_job(job: dict) -> str:
    """Run a single job. Returns the resulting status."""
    module_name = job["module_name"]
    job_id = job["id"]
    project_root = Path(job["project_root"])

    # Check retry limit before running
    if _is_retry_exhausted(job):
        job["status"] = "error"
        job["errors"].append("Maximum retries reached")
        _save_job(job)
        output.error(
            "Job {id}: maximum retries ({max}) reached",
            id=job_id, max=job.get("retry_max"),
            name="jobs.max_retries",
        )
        return "error"

    output.step(
        "Running job: {id} ({module})",
        id=job_id, module=module_name,
        name="jobs.run_start",
    )

    # Verify module exists
    try:
        find_module_path(module_name, project_root=project_root)
    except Exception as e:
        job["errors"].append(str(e))
        job["retry_count"] += 1
        job["last_attempt_at"] = time.time()
        _save_job(job)
        output.error("Module not found: {error}", error=str(e), name="jobs.module_error")
        return "error"

    with tempfile.TemporaryDirectory(prefix="zp-job-") as tmpdir:
        workdir = Path(tmpdir)

        # Copy files from archive to workdir
        before_ids = _copy_files_to_workdir(job, workdir)

        # Build module input
        files_input = []
        for f in job.get("files", []):
            rel = f["relative_path"]
            file_path = workdir / rel
            if not file_path.exists():
                continue
            files_input.append({
                "file_path": str(file_path),
                "config_key": f["config_key"],
                "hashes": f.get("hashes", {}),
                "module_config": job.get("input", {}).get("files", {}).get(f["config_key"], {}),
            })

        if not files_input:
            output.warn("No files to process for this job", name="jobs.no_files")
            job["retry_count"] += 1
            job["last_attempt_at"] = time.time()
            _save_job(job)
            return "pending"

        input_data = {
            "config": job.get("config", {}),
            "output_dir": str(workdir),
            "files": files_input,
        }

        # Run module job subcommand
        try:
            result = run_module_job(
                module_name, input_data, output,
                project_root=project_root,
            )
        except Exception as e:
            job["errors"].append(str(e))
            job["retry_count"] += 1
            job["last_attempt_at"] = time.time()
            _save_job(job)
            output.error(
                "Job failed: {error}", error=str(e), name="jobs.run_error",
            )
            return "error"

        status = result.get("status", "pending")

        # Sync changed files back to archive
        if status in ("complete", "pending"):
            _sync_back_to_archive(job, workdir, before_ids)

        # Update file identifiers in job for next run
        for f in job.get("files", []):
            rel = f["relative_path"]
            file_in_workdir = workdir / rel
            if file_in_workdir.exists():
                f["identifier"] = _sha256(file_in_workdir)

    # Update job state
    job["retry_count"] += 1
    job["last_attempt_at"] = time.time()
    job["status"] = status if status in ("complete", "error") else "pending"
    if status == "error":
        err_msg = result.get("error", "Unknown error")
        job["errors"].append(err_msg)
    _save_job(job)

    if status == "complete":
        output.step_ok("Job complete: {id}", id=job_id, name="jobs.complete")
    elif status == "pending":
        output.step_warn(
            "Job still pending: {id} (retry #{n})",
            id=job_id, n=job["retry_count"],
            name="jobs.still_pending",
        )
    else:
        output.error("Job error: {id}", id=job_id, name="jobs.error_status")

    return status


def run_jobs(*, run_all: bool = False, job_id: str | None = None) -> None:
    """Run pending jobs. If job_id given, run only that one."""
    if job_id:
        job = get_job(job_id)
        if job is None:
            output.error("Job not found: {id}", id=job_id, name="jobs.not_found")
            return
        run_single_job(job)
        return

    jobs = list_jobs(status_filter="pending")
    if not jobs:
        output.info("No pending jobs.", name="jobs.none")
        return

    print_jobs_table(jobs)
    output.info("", name="jobs.blank")

    eligible = [j for j in jobs if run_all or _is_eligible(j)]
    if not eligible:
        output.info("No jobs eligible yet (retry intervals not elapsed).", name="jobs.none_eligible")
        return

    output.info(
        "{n} job(s) eligible to run.", n=len(eligible), name="jobs.eligible_count",
    )

    for job in eligible:
        run_single_job(job)


def clean_jobs() -> int:
    """Remove completed jobs. Returns count removed."""
    removed = 0
    for p in _jobs_dir().glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("status") == "complete":
                p.unlink()
                removed += 1
        except (json.JSONDecodeError, KeyError):
            continue
    return removed
