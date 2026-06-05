"""Tests for the async jobs system (release_tool/jobs.py).

Module-agnostic tests using a mock job_test_module.
All tests use tmp_path — no external GitHub repo needed.
"""

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

import release_tool.jobs as jobs_mod
from release_tool.jobs import (
    create_job,
    list_jobs,
    get_job,
    remove_job,
    count_pending,
    clean_jobs,
    run_single_job,
    run_jobs,
    print_jobs_table,
    print_job_info,
    _is_eligible,
    _is_retry_exhausted,
    _parse_interval,
    _sync_back_to_archive,
    _copy_files_to_workdir,
    _sha256,
    DEFAULT_RETRY_MAX,
)
from release_tool.output import PromptResult

pytestmark = pytest.mark.no_auto_reset

# ---------------------------------------------------------------------------
# Mock module source
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

def _install_module(project_dir: Path) -> Path:
    d = project_dir / ".zp" / "modules" / "job_test_module"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pyproject.toml").write_text(JOB_TEST_MODULE_PYPROJECT)
    p = d / "job_test_module.py"
    p.write_text(JOB_TEST_MODULE_SOURCE)
    return p


def _create_archive(archive_dir: Path, tag: str, module_name: str,
                     files: dict[str, str]) -> Path:
    tag_dir = archive_dir / tag
    mod_dir = tag_dir / module_name
    mod_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (mod_dir / name).write_text(content)
    return tag_dir


def _sha256_str(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _make_job(jobs_dir: Path, *,
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
              errors: list[str] | None = None) -> dict:
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
    job["_path"] = str(path)
    return job


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def jobs_dir(tmp_path, monkeypatch):
    d = tmp_path / "zp_jobs"
    d.mkdir()
    monkeypatch.setattr(jobs_mod, "JOBS_DIR", d)
    return d


@pytest.fixture
def project_env(tmp_path):
    project_root = tmp_path / "project"
    project_root.mkdir()
    _install_module(project_root)
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    return project_root, archive_dir


# ===================================================================
# CRUD tests
# ===================================================================

def test_create_job_writes_valid_json(jobs_dir):
    path = create_job(
        module_name="job_test_module",
        job_descriptor={"description": "desc", "retry_interval": "1h", "retry_max": None},
        tag_name="v1.0.0",
        project_root=Path("/tmp/proj"),
        archive_dir=Path("/tmp/arch"),
        config={"identity_hash_algo": "sha256"},
        files=[{"relative_path": "job_test_module/f.stamp", "config_key": "paper",
                "identifier": "aaa", "hashes": {}, "module_config": {"x": 1}}],
    )
    assert path.exists()
    job = json.loads(path.read_text())
    assert job["status"] == "pending"
    assert job["retry_count"] == 0
    assert job["last_attempt_at"] is None
    assert job["errors"] == []
    assert job["module_name"] == "job_test_module"
    assert job["tag_name"] == "v1.0.0"
    assert job["retry_interval_seconds"] == 3600
    assert job["retry_max"] is None
    assert job["description"] == "desc"


def test_create_job_input_separation(jobs_dir):
    path = create_job(
        module_name="m",
        job_descriptor={"description": "d", "retry_interval": 60},
        tag_name="v1",
        project_root=Path("/p"),
        archive_dir=None,
        config={},
        files=[
            {"relative_path": "m/a.txt", "config_key": "paper",
             "identifier": "x", "hashes": {}, "module_config": {"opt": True}},
            {"relative_path": "m/b.txt", "config_key": "manifest",
             "identifier": "y", "hashes": {}, "module_config": {"opt": False}},
        ],
    )
    job = json.loads(path.read_text())
    # Root files should NOT have module_config
    for f in job["files"]:
        assert "module_config" not in f
    # input.files keyed by config_key
    assert job["input"]["files"]["paper"] == {"opt": True}
    assert job["input"]["files"]["manifest"] == {"opt": False}
    # input.job_descriptor is the raw descriptor
    assert job["input"]["job_descriptor"]["description"] == "d"


def test_create_job_retry_interval_parsing(jobs_dir):
    for raw, expected in [("1h", 3600), ("30m", 1800), ("5min", 300), (60, 60), ("90s", 90)]:
        path = create_job(
            module_name="m",
            job_descriptor={"description": "", "retry_interval": raw},
            tag_name="v1", project_root=Path("/p"), archive_dir=None,
            config={}, files=[],
        )
        job = json.loads(path.read_text())
        assert job["retry_interval_seconds"] == expected, f"Failed for {raw}"


def test_create_job_retry_max_null(jobs_dir):
    path = create_job(
        module_name="m",
        job_descriptor={"description": "", "retry_max": None},
        tag_name="v1", project_root=Path("/p"), archive_dir=None,
        config={}, files=[],
    )
    job = json.loads(path.read_text())
    assert job["retry_max"] is None


def test_create_job_retry_max_default(jobs_dir):
    path = create_job(
        module_name="m",
        job_descriptor={"description": ""},
        tag_name="v1", project_root=Path("/p"), archive_dir=None,
        config={}, files=[],
    )
    job = json.loads(path.read_text())
    assert job["retry_max"] == DEFAULT_RETRY_MAX


def test_create_job_safe_parse_description(jobs_dir):
    path = create_job(
        module_name="m",
        job_descriptor={"description": 12345},
        tag_name="v1", project_root=Path("/p"), archive_dir=None,
        config={}, files=[],
    )
    job = json.loads(path.read_text())
    assert job["description"] == "12345"
    assert isinstance(job["description"], str)


def test_list_jobs_returns_all(jobs_dir):
    _make_job(jobs_dir, status="pending")
    _make_job(jobs_dir, status="complete")
    _make_job(jobs_dir, status="error")
    assert len(list_jobs()) == 3


def test_list_jobs_status_filter(jobs_dir):
    _make_job(jobs_dir, status="pending")
    _make_job(jobs_dir, status="complete")
    _make_job(jobs_dir, status="error")
    assert len(list_jobs(status_filter="pending")) == 1
    assert len(list_jobs(status_filter="complete")) == 1


def test_get_job_exact_id(jobs_dir):
    j = _make_job(jobs_dir)
    found = get_job(j["id"])
    assert found is not None
    assert found["id"] == j["id"]


def test_get_job_prefix_match(jobs_dir):
    j = _make_job(jobs_dir)
    found = get_job(j["id"][:5])
    assert found is not None
    assert found["id"] == j["id"]


def test_get_job_not_found(jobs_dir):
    assert get_job("nonexistent") is None


def test_remove_job(jobs_dir):
    j = _make_job(jobs_dir)
    assert remove_job(j["id"]) is True
    assert not Path(j["_path"]).exists()


def test_remove_job_not_found(jobs_dir):
    assert remove_job("nonexistent") is False


def test_count_pending(jobs_dir):
    _make_job(jobs_dir, status="pending")
    _make_job(jobs_dir, status="pending")
    _make_job(jobs_dir, status="complete")
    _make_job(jobs_dir, status="error")
    assert count_pending() == 2


# ===================================================================
# Display tests
# ===================================================================

def test_print_jobs_table_columns(jobs_dir, capsys):
    _make_job(jobs_dir, description="Job A")
    _make_job(jobs_dir, description="Job B", tag_name="v2.0.0")
    # print_jobs_table uses output.info which prints to stdout
    print_jobs_table(list_jobs())
    out = capsys.readouterr().out
    assert "ID" in out
    assert "MODULE" in out
    assert "STATUS" in out
    assert "DESCRIPTION" in out
    assert "Job A" in out
    assert "Job B" in out


def test_print_job_info_details(jobs_dir, capsys):
    j = _make_job(jobs_dir, description="My desc", errors=["err1"])
    print_job_info(j)
    out = capsys.readouterr().out
    assert j["id"] in out
    assert "job_test_module" in out
    assert "My desc" in out
    assert "err1" in out


def test_print_jobs_table_empty(jobs_dir, capsys):
    print_jobs_table([])
    out = capsys.readouterr().out
    assert "No jobs found" in out


# ===================================================================
# Eligibility tests
# ===================================================================

def test_eligible_first_run(jobs_dir):
    j = _make_job(jobs_dir, last_attempt_at=None)
    assert _is_eligible(j) is True


def test_eligible_after_interval(jobs_dir):
    j = _make_job(jobs_dir, last_attempt_at=time.time() - 7200, retry_interval=3600)
    assert _is_eligible(j) is True


def test_not_eligible_before_interval(jobs_dir):
    j = _make_job(jobs_dir, last_attempt_at=time.time(), retry_interval=3600)
    assert _is_eligible(j) is False


def test_retry_max_reached(jobs_dir, project_env):
    project_root, archive_dir = project_env
    j = _make_job(
        jobs_dir,
        retry_count=5, retry_max=5,
        project_root=str(project_root),
        archive_dir=str(archive_dir),
    )
    status = run_single_job(j)
    assert status == "error"
    # Re-read from disk
    updated = json.loads(Path(j["_path"]).read_text())
    assert updated["status"] == "error"
    assert "Maximum retries reached" in updated["errors"]


def test_retry_max_null_unlimited(jobs_dir):
    j = _make_job(jobs_dir, retry_max=None, retry_count=9999,
                  last_attempt_at=time.time() - 9999)
    assert _is_retry_exhausted(j) is False
    assert _is_eligible(j) is True


# ===================================================================
# Runner tests
# ===================================================================

def test_run_job_complete(jobs_dir, project_env):
    project_root, archive_dir = project_env
    tag = "v1.0.0"
    content = "original:test.stamp"
    _create_archive(archive_dir, tag, "job_test_module", {"test.stamp": content})

    j = _make_job(
        jobs_dir,
        project_root=str(project_root),
        archive_dir=str(archive_dir),
        tag_name=tag,
        files=[{
            "relative_path": "job_test_module/test.stamp",
            "config_key": "project",
            "identifier": _sha256_str(content),
            "hashes": {},
        }],
        input_files={"project": {"job_status": "complete"}},
    )
    status = run_single_job(j)
    assert status == "complete"
    updated = json.loads(Path(j["_path"]).read_text())
    assert updated["status"] == "complete"
    assert updated["retry_count"] == 1
    assert updated["last_attempt_at"] is not None


def test_run_job_pending(jobs_dir, project_env):
    project_root, archive_dir = project_env
    tag = "v1.0.0"
    content = "original"
    _create_archive(archive_dir, tag, "job_test_module", {"test.stamp": content})

    j = _make_job(
        jobs_dir,
        project_root=str(project_root),
        archive_dir=str(archive_dir),
        tag_name=tag,
        files=[{
            "relative_path": "job_test_module/test.stamp",
            "config_key": "project",
            "identifier": _sha256_str(content),
            "hashes": {},
        }],
        input_files={"project": {"job_status": "pending"}},
    )
    status = run_single_job(j)
    assert status == "pending"
    updated = json.loads(Path(j["_path"]).read_text())
    assert updated["status"] == "pending"
    assert updated["retry_count"] == 1


def test_run_job_error(jobs_dir, project_env):
    project_root, archive_dir = project_env
    tag = "v1.0.0"
    content = "original"
    _create_archive(archive_dir, tag, "job_test_module", {"test.stamp": content})

    j = _make_job(
        jobs_dir,
        project_root=str(project_root),
        archive_dir=str(archive_dir),
        tag_name=tag,
        files=[{
            "relative_path": "job_test_module/test.stamp",
            "config_key": "project",
            "identifier": _sha256_str(content),
            "hashes": {},
        }],
        input_files={"project": {"job_status": "error", "error_message": "boom"}},
    )
    status = run_single_job(j)
    assert status == "error"
    updated = json.loads(Path(j["_path"]).read_text())
    assert updated["status"] == "error"


def test_run_job_multiple_config_keys(jobs_dir, project_env):
    project_root, archive_dir = project_env
    tag = "v1.0.0"
    _create_archive(archive_dir, tag, "job_test_module", {
        "paper.stamp": "paper_content",
        "manifest.stamp": "manifest_content",
    })

    j = _make_job(
        jobs_dir,
        project_root=str(project_root),
        archive_dir=str(archive_dir),
        tag_name=tag,
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
    status = run_single_job(j)
    assert status == "complete"


def test_run_job_crash_resilience(jobs_dir, project_env, monkeypatch):
    project_root, archive_dir = project_env
    tag = "v1.0.0"
    content = "original"
    _create_archive(archive_dir, tag, "job_test_module", {"test.stamp": content})

    j = _make_job(
        jobs_dir,
        project_root=str(project_root),
        archive_dir=str(archive_dir),
        tag_name=tag,
        files=[{
            "relative_path": "job_test_module/test.stamp",
            "config_key": "project",
            "identifier": _sha256_str(content),
            "hashes": {},
        }],
        input_files={"project": {}},
    )

    # Make run_module_job raise
    from release_tool import modules
    def _raise(*a, **kw):
        raise RuntimeError("crash!")
    monkeypatch.setattr(modules, "run_module_job", _raise)
    monkeypatch.setattr(jobs_mod, "run_module_job", _raise)

    status = run_single_job(j)
    assert status == "error"
    # Job file still exists and is valid
    updated = json.loads(Path(j["_path"]).read_text())
    assert updated["retry_count"] == 1
    assert "crash!" in updated["errors"][0]


def test_run_job_identifier_updated(jobs_dir, project_env, monkeypatch):
    project_root, archive_dir = project_env
    tag = "v1.0.0"
    original = "original"
    _create_archive(archive_dir, tag, "job_test_module", {"test.stamp": original})
    old_id = _sha256_str(original)

    # Mock prompt for overwrite (module modifies the file)
    from release_tool import prompts
    monkeypatch.setattr(
        prompts.confirm_job_overwrite, "ask",
        lambda msg: PromptResult(name="confirm_job_overwrite", is_accept=True, value="yes"),
    )

    j = _make_job(
        jobs_dir,
        project_root=str(project_root),
        archive_dir=str(archive_dir),
        tag_name=tag,
        files=[{
            "relative_path": "job_test_module/test.stamp",
            "config_key": "project",
            "identifier": old_id,
            "hashes": {},
        }],
        input_files={"project": {"job_status": "complete", "modify_file": True}},
    )
    run_single_job(j)
    updated = json.loads(Path(j["_path"]).read_text())
    new_id = updated["files"][0]["identifier"]
    assert new_id != old_id


# ===================================================================
# Sync tests
# ===================================================================

def _run_sync_test(tmp_path, monkeypatch, prompt_value, modify=True):
    """Helper: set up archive, build workdir, modify file, run _sync_back_to_archive."""
    archive_dir = tmp_path / "archive"
    tag = "v1.0.0"
    module_name = "job_test_module"
    original_content = "original_content"
    _create_archive(archive_dir, tag, module_name, {"file.stamp": original_content})
    archive_file = archive_dir / tag / module_name / "file.stamp"
    original_mtime = archive_file.stat().st_mtime

    # Build workdir mirroring archive structure
    workdir = tmp_path / "workdir"
    mod_dir = workdir / module_name
    mod_dir.mkdir(parents=True)
    workdir_file = mod_dir / "file.stamp"
    if modify:
        workdir_file.write_text("modified_content")
    else:
        workdir_file.write_text(original_content)

    before_ids = {"job_test_module/file.stamp": _sha256_str(original_content)}

    job = {
        "archive_dir": str(archive_dir),
        "tag_name": tag,
        "module_name": module_name,
        "files": [{"relative_path": "job_test_module/file.stamp", "config_key": "project"}],
    }

    from release_tool import prompts
    monkeypatch.setattr(
        prompts.confirm_job_overwrite, "ask",
        lambda msg: PromptResult(name="confirm_job_overwrite",
                                  is_accept=(prompt_value != "no"),
                                  value=prompt_value),
    )

    _sync_back_to_archive(job, workdir, before_ids)
    return archive_file, original_mtime, original_content


def test_sync_overwrite_yes(tmp_path, monkeypatch):
    archive_file, _, _ = _run_sync_test(tmp_path, monkeypatch, "yes")
    assert archive_file.read_text() == "modified_content"


def test_sync_overwrite_no(tmp_path, monkeypatch):
    archive_file, original_mtime, original_content = _run_sync_test(
        tmp_path, monkeypatch, "no"
    )
    assert archive_file.read_text() == original_content
    # mtime should not have changed
    assert archive_file.stat().st_mtime == original_mtime


def test_sync_backup(tmp_path, monkeypatch):
    archive_file, _, original_content = _run_sync_test(tmp_path, monkeypatch, "backup")
    # Original file has new content
    assert archive_file.read_text() == "modified_content"
    # Backup has old content
    backup = archive_file.with_suffix(".stamp.backup")
    assert backup.exists()
    assert backup.read_text() == original_content


def test_sync_new_file_archived(tmp_path, monkeypatch):
    archive_dir = tmp_path / "archive"
    tag = "v1.0.0"
    module_name = "job_test_module"
    tag_dir = archive_dir / tag / module_name
    tag_dir.mkdir(parents=True)

    workdir = tmp_path / "workdir"
    mod_dir = workdir / module_name
    mod_dir.mkdir(parents=True)
    (mod_dir / "new_file.txt").write_text("new content")

    job = {"archive_dir": str(archive_dir), "tag_name": tag,
           "module_name": module_name, "files": []}

    _sync_back_to_archive(job, workdir, {})
    assert (tag_dir / "new_file.txt").exists()
    assert (tag_dir / "new_file.txt").read_text() == "new content"


def test_sync_unchanged_skipped(tmp_path, monkeypatch):
    archive_file, original_mtime, original_content = _run_sync_test(
        tmp_path, monkeypatch, "yes", modify=False
    )
    # File not modified → no overwrite, content unchanged
    assert archive_file.read_text() == original_content


def test_sync_archive_isolation(tmp_path, monkeypatch):
    archive_dir = tmp_path / "archive"
    tag = "v1.0.0"
    module_name = "job_test_module"

    # Create files in module dir AND another dir
    _create_archive(archive_dir, tag, module_name, {"file.stamp": "module_content"})
    other_dir = archive_dir / tag / "other_module"
    other_dir.mkdir(parents=True)
    (other_dir / "other.txt").write_text("untouched")
    other_mtime = (other_dir / "other.txt").stat().st_mtime

    # Workdir with modified module file
    workdir = tmp_path / "workdir"
    mod_dir = workdir / module_name
    mod_dir.mkdir(parents=True)
    (mod_dir / "file.stamp").write_text("modified")

    job = {"archive_dir": str(archive_dir), "tag_name": tag,
           "module_name": module_name, "files": []}

    from release_tool import prompts
    monkeypatch.setattr(
        prompts.confirm_job_overwrite, "ask",
        lambda msg: PromptResult(name="confirm_job_overwrite", is_accept=True, value="yes"),
    )

    _sync_back_to_archive(job, workdir, {})

    # Module file updated
    assert (archive_dir / tag / module_name / "file.stamp").read_text() == "modified"
    # Other dir completely untouched
    assert (other_dir / "other.txt").read_text() == "untouched"
    assert (other_dir / "other.txt").stat().st_mtime == other_mtime


# ===================================================================
# Clean tests
# ===================================================================

def test_clean_removes_completed_only(jobs_dir):
    _make_job(jobs_dir, status="pending")
    _make_job(jobs_dir, status="complete")
    _make_job(jobs_dir, status="error")
    removed = clean_jobs()
    assert removed == 1
    remaining = list_jobs()
    assert len(remaining) == 2
    assert all(j["status"] != "complete" for j in remaining)


def test_clean_returns_count(jobs_dir):
    _make_job(jobs_dir, status="complete")
    _make_job(jobs_dir, status="complete")
    _make_job(jobs_dir, status="complete")
    assert clean_jobs() == 3


# ===================================================================
# Multiple jobs / isolation
# ===================================================================

def test_multiple_jobs_same_module(jobs_dir):
    j1 = _make_job(jobs_dir, tag_name="v1.0.0")
    j2 = _make_job(jobs_dir, tag_name="v2.0.0")
    jobs = list_jobs()
    assert len(jobs) == 2
    ids = {j["id"] for j in jobs}
    assert j1["id"] in ids
    assert j2["id"] in ids


def test_tag_isolation(jobs_dir, project_env, monkeypatch):
    project_root, archive_dir = project_env
    # Two tags with their own archives
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"f.stamp": "v1_content"})
    _create_archive(archive_dir, "v2.0.0", "job_test_module", {"f.stamp": "v2_content"})

    # Mock prompt for overwrite (module modifies the file)
    from release_tool import prompts
    monkeypatch.setattr(
        prompts.confirm_job_overwrite, "ask",
        lambda msg: PromptResult(name="confirm_job_overwrite", is_accept=True, value="yes"),
    )

    j1 = _make_job(
        jobs_dir, tag_name="v1.0.0",
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/f.stamp", "config_key": "project",
                "identifier": _sha256_str("v1_content"), "hashes": {}}],
        input_files={"project": {"job_status": "complete", "modify_file": True}},
    )
    run_single_job(j1)

    # v2 archive untouched
    v2_file = archive_dir / "v2.0.0" / "job_test_module" / "f.stamp"
    assert v2_file.read_text() == "v2_content"


def test_jobs_use_tempdir_not_cache(jobs_dir, project_env):
    project_root, archive_dir = project_env
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"test.stamp": content})

    j = _make_job(
        jobs_dir,
        project_root=str(project_root), archive_dir=str(archive_dir),
        tag_name="v1.0.0",
        files=[{"relative_path": "job_test_module/test.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )
    run_single_job(j)

    # No .zp/cache/ created
    cache_dir = project_root / ".zp" / "cache"
    assert not cache_dir.exists()


# ===================================================================
# run_jobs integration
# ===================================================================

def test_run_jobs_eligible_only(jobs_dir, project_env):
    project_root, archive_dir = project_env
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"a.stamp": content})
    _create_archive(archive_dir, "v2.0.0", "job_test_module", {"b.stamp": content})

    # Eligible: no last_attempt
    j1 = _make_job(
        jobs_dir, tag_name="v1.0.0",
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/a.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )
    # Not eligible: just attempted
    j2 = _make_job(
        jobs_dir, tag_name="v2.0.0",
        last_attempt_at=time.time(), retry_interval=9999,
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/b.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )

    run_jobs()

    u1 = json.loads(Path(j1["_path"]).read_text())
    u2 = json.loads(Path(j2["_path"]).read_text())
    assert u1["status"] == "complete"  # ran
    assert u2["status"] == "pending"   # skipped
    assert u2["retry_count"] == 0      # not touched


def test_run_jobs_all_flag(jobs_dir, project_env):
    project_root, archive_dir = project_env
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"a.stamp": content})
    _create_archive(archive_dir, "v2.0.0", "job_test_module", {"b.stamp": content})

    j1 = _make_job(
        jobs_dir, tag_name="v1.0.0",
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/a.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )
    j2 = _make_job(
        jobs_dir, tag_name="v2.0.0",
        last_attempt_at=time.time(), retry_interval=9999,
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/b.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )

    run_jobs(run_all=True)

    u1 = json.loads(Path(j1["_path"]).read_text())
    u2 = json.loads(Path(j2["_path"]).read_text())
    assert u1["status"] == "complete"
    assert u2["status"] == "complete"


def test_run_jobs_specific_id(jobs_dir, project_env):
    project_root, archive_dir = project_env
    content = "original"
    _create_archive(archive_dir, "v1.0.0", "job_test_module", {"a.stamp": content})
    _create_archive(archive_dir, "v2.0.0", "job_test_module", {"b.stamp": content})

    j1 = _make_job(
        jobs_dir, tag_name="v1.0.0",
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/a.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )
    j2 = _make_job(
        jobs_dir, tag_name="v2.0.0",
        project_root=str(project_root), archive_dir=str(archive_dir),
        files=[{"relative_path": "job_test_module/b.stamp", "config_key": "project",
                "identifier": _sha256_str(content), "hashes": {}}],
        input_files={"project": {"job_status": "complete"}},
    )

    run_jobs(job_id=j1["id"])

    u1 = json.loads(Path(j1["_path"]).read_text())
    u2 = json.loads(Path(j2["_path"]).read_text())
    assert u1["status"] == "complete"
    assert u2["status"] == "pending"  # not touched


def test_run_jobs_not_found(jobs_dir, capsys):
    run_jobs(job_id="nonexistent")
    out = capsys.readouterr().out
    assert "not found" in out.lower() or "Not found" in out


# ===================================================================
# Pending notice
# ===================================================================

def test_count_pending_with_jobs(jobs_dir):
    assert count_pending() == 0
    _make_job(jobs_dir, status="pending")
    _make_job(jobs_dir, status="pending")
    _make_job(jobs_dir, status="complete")
    assert count_pending() == 2
