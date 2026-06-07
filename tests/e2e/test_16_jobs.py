"""E2E tests for the async jobs system via ZpRunner.

All tests run through the CLI (zp jobs ...) as subprocess, matching the
project's E2E test conventions. Jobs directory isolated via ZP_JOBS_DIR env var.
"""

import hashlib
import json
import time
from pathlib import Path

import pytest

from tests.utils.cli import ZpRunner
from tests.utils.ndjson import find_by_name, find_all_by_name

pytestmark = pytest.mark.no_auto_reset


def _find_job_event(events: list[dict], name: str, job_id: str) -> dict | None:
    """Find an event by name that matches a specific job ID in data.id."""
    for e in events:
        e_name = e.get("name", "")
        if e_name == name or e_name.startswith(f"{name}."):
            if e.get("data", {}).get("id") == job_id:
                return e
    return None

# ---------------------------------------------------------------------------
# Mock module
# ---------------------------------------------------------------------------

JOB_TEST_MODULE_PYPROJECT = """\
[project]
name = "job-test-module"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []
"""

JOB_TEST_MODULE_SOURCE = """\
import argparse
import json
import sys
from pathlib import Path


def emit(type_, msg, name="", **kwargs):
    event = {"type": type_, "msg": msg, "name": name}
    if kwargs:
        event["data"] = kwargs
    print(json.dumps(event), flush=True)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    check_p = sub.add_parser("check")
    check_p.add_argument("--config")
    run_p = sub.add_parser("run")
    run_p.add_argument("--input", required=True)
    job_p = sub.add_parser("job")
    job_p.add_argument("--input", required=True)
    args = parser.parse_args()

    if args.command == "check":
        emit("detail_ok", "check ok", name="job_test_module.check.ok")
        return

    if args.command == "run":
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
        output_dir = Path(data["output_dir"])
        result_files = []
        job_desc = None
        for fi in data["files"]:
            fp = Path(fi["file_path"])
            mc = fi.get("module_config", {})
            out = output_dir / f"{fp.name}.stamp"
            out.write_text(f"stamp:{fp.name}")
            result_files.append({
                "file_path": str(out),
                "config_key": fi["config_key"],
                "module_entry_type": "stamp",
                "module_config": mc,
            })
            if job_desc is None:
                job_desc = mc.get("job_descriptor")
        result = {"type": "result", "files": result_files}
        if job_desc:
            result["job"] = job_desc
        print(json.dumps(result), flush=True)
        return

    if args.command == "job":
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
        overall = "complete"
        for fi in data.get("files", []):
            fp = Path(fi["file_path"])
            mc = fi.get("module_config", {})
            status = mc.get("job_status", "complete")
            if status == "error":
                overall = "error"
            elif status == "pending" and overall != "error":
                overall = "pending"
            if mc.get("modify_file") and fp.exists():
                fp.write_text(f"modified:{fp.name}")
            if mc.get("create_new_file"):
                new_name = mc.get("new_file_name", f"{fp.stem}.extra{fp.suffix}")
                (fp.parent / new_name).write_text(f"new:{new_name}")
            emit("detail_ok", f"job processed {fp.name}", name="job_test_module.job_done")
        print(json.dumps({"type": "result", "status": overall}), flush=True)
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _install_module(project_dir: Path):
    d = project_dir / ".zp" / "modules" / "job_test_module"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pyproject.toml").write_text(JOB_TEST_MODULE_PYPROJECT)
    (d / "job_test_module.py").write_text(JOB_TEST_MODULE_SOURCE)


def _create_archive(archive_dir: Path, tag: str, module_name: str,
                     files: dict[str, str]):
    mod_dir = archive_dir / tag / module_name
    mod_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (mod_dir / name).write_text(content)


def _sha256_str(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _write_job(jobs_dir: Path, *,
               module_name: str = "job_test_module",
               tag_name: str = "v1.0.0",
               project_root: str = "/tmp/project",
               archive_dir: str = "/tmp/archive",
               status: str = "pending",
               retry_interval: int = 3600,
               retry_max: int | None = 100,
               retry_count: int = 0,
               last_attempt_at: float | None = None,
               description: str = "Test job",
               files: list[dict] | None = None,
               input_files: dict | None = None,
               errors: list[str] | None = None) -> tuple[str, Path]:
    """Write a job JSON file. Returns (job_id, path)."""
    import uuid
    job_id = uuid.uuid4().hex[:10]
    if files is None:
        files = [{
            "relative_path": f"{module_name}/test.stamp",
            "config_key": "project",
            "identifier": "abc123",
            "module_entry_type": "stamp",
            "hashes": {},
        }]
    job = {
        "id": job_id,
        "module_name": module_name,
        "tag_name": tag_name,
        "project_root": project_root,
        "archive_dir": archive_dir,
        "created_at": time.time(),
        "status": status,
        "retry_interval_seconds": retry_interval,
        "retry_count": retry_count,
        "retry_max": retry_max,
        "description": description,
        "last_attempt_at": last_attempt_at,
        "errors": errors or [],
        "config": {"identity_hash_algo": "sha256"},
        "files": files,
        "input": {
            "job_descriptor": {
                "description": description,
                "retry_interval": f"{retry_interval}s",
                "retry_max": retry_max,
            },
            "files": input_files or {},
        },
    }
    path = jobs_dir / f"{job_id}.json"
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    return job_id, path


def _read_job(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _test_config(prompts: dict | None = None) -> dict:
    tc = {"verify_prompts": False}
    if prompts:
        tc["prompts"] = prompts
    return tc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def env_setup(tmp_path):
    """Create isolated jobs dir, project with module, archive, and runner."""
    jobs_dir = tmp_path / "zp_jobs"
    jobs_dir.mkdir()
    project_root = tmp_path / "project"
    project_root.mkdir()
    _install_module(project_root)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    runner = ZpRunner(project_root)
    env = {"ZP_JOBS_DIR": str(jobs_dir)}
    return runner, jobs_dir, project_root, archive_dir, env


# ===================================================================
# CRUD via CLI
# ===================================================================

def test_jobs_list_empty(env_setup, fix_log_path):
    runner, _, _, _, env = env_setup
    result = runner.run_test("jobs", extra_args=["list"],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    assert find_by_name(result.events, "jobs.empty")


def test_jobs_list_shows_all(env_setup, fix_log_path):
    runner, jobs_dir, _, _, env = env_setup
    id1, _ = _write_job(jobs_dir, tag_name="v1.0.0", description="Job A")
    id2, _ = _write_job(jobs_dir, tag_name="v2.0.0", description="Job B")
    result = runner.run_test("jobs", extra_args=["list"],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    rows = find_all_by_name(result.events, "jobs.row")
    assert len(rows) == 2
    texts = " ".join(r["msg"] for r in rows)
    assert id1 in texts
    assert id2 in texts


def test_jobs_info(env_setup, fix_log_path):
    runner, jobs_dir, _, _, env = env_setup
    job_id, _ = _write_job(jobs_dir, description="My detailed job")
    result = runner.run_test("jobs", extra_args=["info", job_id],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    id_event = find_by_name(result.events, "jobs.info.id")
    assert id_event
    assert id_event.get("data", {}).get("id") == job_id
    desc_event = find_by_name(result.events, "jobs.info.description")
    assert desc_event
    assert desc_event.get("data", {}).get("v") == "My detailed job"


def test_jobs_rm(env_setup, fix_log_path):
    runner, jobs_dir, _, _, env = env_setup
    job_id, path = _write_job(jobs_dir)
    assert path.exists()
    result = runner.run_test("jobs", extra_args=["rm", job_id],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    assert find_by_name(result.events, "jobs.removed")
    assert not path.exists()


def test_jobs_rm_not_found(env_setup, fix_log_path):
    runner, _, _, _, env = env_setup
    result = runner.run_test("jobs", extra_args=["rm", "nonexistent"],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    assert find_by_name(result.events, "jobs.not_found")


def test_jobs_clean(env_setup, fix_log_path):
    runner, jobs_dir, _, _, env = env_setup
    _, p1 = _write_job(jobs_dir, status="pending")
    _, p2 = _write_job(jobs_dir, status="complete")
    _, p3 = _write_job(jobs_dir, status="error")
    result = runner.run_test("jobs", extra_args=["clean"],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    assert find_by_name(result.events, "jobs.cleaned")
    assert p1.exists()      # pending kept
    assert not p2.exists()  # complete removed
    assert p3.exists()      # error kept


def test_jobs_default_is_list(env_setup, fix_log_path):
    runner, jobs_dir, _, _, env = env_setup
    _write_job(jobs_dir, description="visible")
    result = runner.run_test("jobs",
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    rows = find_all_by_name(result.events, "jobs.row")
    assert len(rows) == 1
    assert "visible" in rows[0]["msg"]


# ===================================================================
# Job execution via CLI
# ===================================================================

def test_jobs_run_complete(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"test.stamp": content})
    job_id, path = _write_job(
        jobs_dir,
        project_root=str(project_root), archive_dir=str(archive_dir),
        tag_name="v1.0.0",
        files=[{"relative_path": "job_test_module/test.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )
    result = runner.run_test("jobs", extra_args=["run"],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    assert _find_job_event(result.events, "jobs.complete", job_id)
    job = _read_job(path)
    assert job["status"] == "complete"
    assert job["retry_count"] == 1


def test_jobs_run_pending(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"test.stamp": content})
    job_id, path = _write_job(
        jobs_dir,
        project_root=str(project_root), archive_dir=str(archive_dir),
        tag_name="v1.0.0",
        files=[{"relative_path": "job_test_module/test.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "pending"}},
    )
    result = runner.run_test("jobs", extra_args=["run"],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    assert _find_job_event(result.events, "jobs.still_pending", job_id)
    job = _read_job(path)
    assert job["status"] == "pending"
    assert job["retry_count"] == 1


def test_jobs_run_specific_id(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"a.stamp": content})
    _create_archive(archive_dir, "v2.0.0", "job_test_module", {"b.stamp": content})
    id1, p1 = _write_job(
        jobs_dir, tag_name="v1.0.0",
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/a.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )
    id2, p2 = _write_job(
        jobs_dir, tag_name="v2.0.0",
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/b.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )
    result = runner.run_test("jobs", extra_args=["run", id1],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    assert _find_job_event(result.events, "jobs.complete", id1)
    assert not _find_job_event(result.events, "jobs.complete", id2)
    assert _read_job(p1)["status"] == "complete"
    assert _read_job(p2)["status"] == "pending"  # untouched


def test_jobs_run_all(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"a.stamp": content})
    _create_archive(archive_dir, "v2.0.0", "job_test_module", {"b.stamp": content})
    _, p1 = _write_job(
        jobs_dir, tag_name="v1.0.0",
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/a.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )
    _, p2 = _write_job(
        jobs_dir, tag_name="v2.0.0",
        last_attempt_at=time.time(), retry_interval=9999,
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/b.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )
    result = runner.run_test("jobs", extra_args=["run", "--all"],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    assert _read_job(p1)["status"] == "complete"
    assert _read_job(p2)["status"] == "complete"


def test_jobs_run_not_eligible(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"test.stamp": content})
    _, path = _write_job(
        jobs_dir,
        last_attempt_at=time.time(), retry_interval=9999,
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/test.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )
    result = runner.run_test("jobs", extra_args=["run"],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    assert find_by_name(result.events, "jobs.none_eligible")
    assert _read_job(path)["retry_count"] == 0  # not touched


# ===================================================================
# Sync via CLI
# ===================================================================

def test_sync_overwrite_yes(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"test.stamp": content})
    _write_job(
        jobs_dir,
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/test.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete", "modify_file": True}},
    )
    result = runner.run_test(
        "jobs", extra_args=["run"],
        test_config=_test_config(prompts={"confirm_job_overwrite": "yes"}),
        log_path=fix_log_path, fail_on="ignore", env=env,
    )
    archive_file = archive_dir / "v1.0.0" / "job_test_module" / "test.stamp"
    assert archive_file.read_text() == "modified:test.stamp"


def test_sync_overwrite_no(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"test.stamp": content})
    _write_job(
        jobs_dir,
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/test.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete", "modify_file": True}},
    )
    result = runner.run_test(
        "jobs", extra_args=["run"],
        test_config=_test_config(prompts={"confirm_job_overwrite": "no"}),
        log_path=fix_log_path, fail_on="ignore", env=env,
    )
    archive_file = archive_dir / "v1.0.0" / "job_test_module" / "test.stamp"
    assert archive_file.read_text() == content  # unchanged


def test_sync_backup(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"test.stamp": content})
    _write_job(
        jobs_dir,
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/test.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete", "modify_file": True}},
    )
    result = runner.run_test(
        "jobs", extra_args=["run"],
        test_config=_test_config(prompts={"confirm_job_overwrite": "backup"}),
        log_path=fix_log_path, fail_on="ignore", env=env,
    )
    archive_file = archive_dir / "v1.0.0" / "job_test_module" / "test.stamp"
    backup_file = archive_file.with_suffix(".stamp.backup")
    assert archive_file.read_text() == "modified:test.stamp"  # new content
    assert backup_file.exists()
    assert backup_file.read_text() == content  # old content preserved


def test_sync_new_file(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"test.stamp": content})
    _write_job(
        jobs_dir,
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/test.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete", "create_new_file": True,
                                  "new_file_name": "test.extra.stamp"}},
    )
    result = runner.run_test(
        "jobs", extra_args=["run"],
        test_config=_test_config(), log_path=fix_log_path,
        fail_on="ignore", env=env,
    )
    new_file = archive_dir / "v1.0.0" / "job_test_module" / "test.extra.stamp"
    assert new_file.exists()
    assert new_file.read_text() == "new:test.extra.stamp"


def test_sync_unchanged_no_prompt(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"test.stamp": content})
    # No modify_file → file stays unchanged, no prompt needed
    job_id, _ = _write_job(
        jobs_dir,
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/test.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )
    result = runner.run_test(
        "jobs", extra_args=["run"],
        test_config=_test_config(), log_path=fix_log_path,
        fail_on="ignore", env=env,
    )
    assert _find_job_event(result.events, "jobs.complete", job_id)
    archive_file = archive_dir / "v1.0.0" / "job_test_module" / "test.stamp"
    assert archive_file.read_text() == content  # untouched


# ===================================================================
# Retry / max
# ===================================================================

def test_retry_max_reached(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    job_id, path = _write_job(
        jobs_dir, retry_count=5, retry_max=5,
        project_root=str(project_root), archive_dir=str(archive_dir),
    )
    result = runner.run_test("jobs", extra_args=["run", "--all"],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    assert _find_job_event(result.events, "jobs.max_retries", job_id)
    job = _read_job(path)
    assert job["status"] == "error"
    assert "Maximum retries reached" in job["errors"]


def test_retry_max_null(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"test.stamp": content})
    job_id, path = _write_job(
        jobs_dir, retry_count=9999, retry_max=None,
        last_attempt_at=time.time() - 9999,
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/test.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )
    result = runner.run_test("jobs", extra_args=["run"],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    assert _find_job_event(result.events, "jobs.complete", job_id)
    job = _read_job(path)
    assert job["status"] == "complete"


# ===================================================================
# Isolation
# ===================================================================

def test_archive_isolation(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"test.stamp": content})
    # Create another dir in archive that should not be touched
    other_dir = archive_dir / "v1.0.0" / "other_module"
    other_dir.mkdir(parents=True)
    (other_dir / "keep.txt").write_text("untouched")

    _write_job(
        jobs_dir,
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/test.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete", "modify_file": True}},
    )
    result = runner.run_test(
        "jobs", extra_args=["run"],
        test_config=_test_config(prompts={"confirm_job_overwrite": "yes"}),
        log_path=fix_log_path, fail_on="ignore", env=env,
    )
    # Other dir untouched
    assert (other_dir / "keep.txt").read_text() == "untouched"


def test_tag_isolation(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"f.stamp": "v1_content"})
    _create_archive(archive_dir, "v2.0.0", "job_test_module", {"f.stamp": "v2_content"})

    _write_job(
        jobs_dir, tag_name="v1.0.0",
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/f.stamp", "config_key": "project",
                "identifier": _sha256_str("v1_content"), "hashes": {}}],
        input_files={"project": {"job_status": "complete", "modify_file": True}},
    )
    result = runner.run_test(
        "jobs", extra_args=["run"],
        test_config=_test_config(prompts={"confirm_job_overwrite": "yes"}),
        log_path=fix_log_path, fail_on="ignore", env=env,
    )
    # v2 untouched
    assert (archive_dir / "v2.0.0" / "job_test_module" / "f.stamp").read_text() == "v2_content"


def test_no_cache_created(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"test.stamp": content})
    _write_job(
        jobs_dir,
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/test.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )
    runner.run_test("jobs", extra_args=["run"],
                    test_config=_test_config(), log_path=fix_log_path,
                    fail_on="ignore", env=env)
    assert not (project_root / ".zp" / "cache").exists()


# ===================================================================
# Multiple config_keys
# ===================================================================

def test_multiple_config_keys(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {
        "paper.stamp": "paper_content",
        "manifest.stamp": "manifest_content",
    })
    job_id, _ = _write_job(
        jobs_dir,
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[
            {"relative_path": "job_test_module/paper.stamp", "config_key": "paper",
             "identifier": _sha256_str("paper_content"), "hashes": {}},
            {"relative_path": "job_test_module/manifest.stamp", "config_key": "manifest",
             "identifier": _sha256_str("manifest_content"), "hashes": {}},
        ],
        input_files={
            "paper": {"job_status": "complete"},
            "manifest": {"job_status": "complete"},
        },
    )
    result = runner.run_test("jobs", extra_args=["run"],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    assert _find_job_event(result.events, "jobs.complete", job_id)
    # Both files processed (module emits job_done per file)
    done_events = find_all_by_name(result.events, "job_test_module.job_done")
    assert len(done_events) == 2


# ===================================================================
# Pending notice
# ===================================================================

def test_pending_notice(env_setup, fix_log_path):
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    _write_job(jobs_dir, status="pending")
    _write_job(jobs_dir, status="pending")
    # Run a command that is NOT jobs — should trigger pending notice
    # zp release will fail early (no config) but notice should appear first
    result = runner.run_test("release", test_config=_test_config(),
                             log_path=fix_log_path, fail_on="ignore", env=env)
    assert find_by_name(result.events, "jobs.pending_notice")


# ===================================================================
# Debug mode
# ===================================================================

def test_jobs_run_debug(env_setup, fix_log_path):
    """Verify --debug flag works with zp jobs run (no crash, events emitted)."""
    runner, jobs_dir, project_root, archive_dir, env = env_setup
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"test.stamp": content})
    job_id, path = _write_job(
        jobs_dir,
        project_root=str(project_root), archive_dir=str(archive_dir),
        tag_name="v1.0.0",
        files=[{"relative_path": "job_test_module/test.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )
    result = runner.run_test("jobs", extra_args=["--debug", "run"],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    assert _find_job_event(result.events, "jobs.complete", job_id)
    assert result.returncode == 0


def test_jobs_list_debug(env_setup, fix_log_path):
    """Verify --debug flag works with zp jobs list."""
    runner, jobs_dir, _, _, env = env_setup
    _write_job(jobs_dir, description="debug test")
    result = runner.run_test("jobs", extra_args=["--debug", "list"],
                             test_config=_test_config(), log_path=fix_log_path,
                             fail_on="ignore", env=env)
    rows = find_all_by_name(result.events, "jobs.row")
    assert len(rows) == 1
    assert result.returncode == 0
