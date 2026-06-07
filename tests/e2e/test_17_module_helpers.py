"""Tests for release_tool/modules/_shared.py — shared utilities for built-in modules.

All functions are tested:
  - create_emitter        : NDJSON event emitter factory
  - compute_file_hash     : file hashing (sha256, sha512, etc.)
  - filter_input_files    : file type filtering by input_types
  - run_module_job_files  : job input parsing + status aggregation
  - run_module_files      : module input parsing + handler iteration

All tests use tmp_path — no external repo needed.
"""

import argparse
import io
import json
import hashlib
import sys
from pathlib import Path

import pytest

# _shared.py is not a package module — add its directory to sys.path
_MODULES_DIR = Path(__file__).resolve().parent.parent.parent / "release_tool" / "modules"
if str(_MODULES_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULES_DIR))

from _shared import (
    create_emitter,
    compute_file_hash,
    filter_input_files,
    run_module_job_files,
    run_module_files,
)

pytestmark = pytest.mark.no_auto_reset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture_stdout(fn, *args, **kwargs):
    """Call fn and return captured stdout as string."""
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        fn(*args, **kwargs)
    finally:
        sys.stdout = old
    return buf.getvalue()


def _parse_ndjson(text: str) -> list[dict]:
    """Parse NDJSON lines into a list of dicts."""
    result = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            result.append(json.loads(line))
    return result


def _write_input(tmp_path: Path, data: dict) -> str:
    """Write input JSON and return path string."""
    p = tmp_path / "input.json"
    p.write_text(json.dumps(data))
    return str(p)


# ===========================================================================
# create_emitter
# ===========================================================================

class TestCreateEmitter:

    def test_basic_event(self):
        """Emit produces valid NDJSON with prefixed name."""
        emit = create_emitter("my_module")
        output = _capture_stdout(emit, "detail", "hello world", name="start")
        ev = json.loads(output.strip())
        assert ev["type"] == "detail"
        assert ev["msg"] == "hello world"
        assert ev["name"] == "my_module.start"

    def test_empty_name(self):
        """Emit with no name produces empty string name."""
        emit = create_emitter("mod")
        output = _capture_stdout(emit, "warn", "oops")
        ev = json.loads(output.strip())
        assert ev["name"] == ""

    def test_kwargs_in_data(self):
        """Extra kwargs are stored under 'data' key."""
        emit = create_emitter("mod")
        output = _capture_stdout(emit, "detail", "msg", name="x", foo="bar", count=42)
        ev = json.loads(output.strip())
        assert ev["data"] == {"foo": "bar", "count": 42}

    def test_no_data_without_kwargs(self):
        """No 'data' key when no extra kwargs are passed."""
        emit = create_emitter("mod")
        output = _capture_stdout(emit, "detail", "msg", name="x")
        ev = json.loads(output.strip())
        assert "data" not in ev

    def test_different_module_names(self):
        """Different module names produce different prefixes."""
        emit_a = create_emitter("alpha")
        emit_b = create_emitter("beta")
        out_a = json.loads(_capture_stdout(emit_a, "detail", "m", name="n"))
        out_b = json.loads(_capture_stdout(emit_b, "detail", "m", name="n"))
        assert out_a["name"] == "alpha.n"
        assert out_b["name"] == "beta.n"


# ===========================================================================
# compute_file_hash
# ===========================================================================

class TestComputeFileHash:

    def test_sha256(self, tmp_path):
        """SHA-256 hash matches hashlib reference."""
        f = tmp_path / "test.bin"
        content = b"hello world"
        f.write_bytes(content)
        result = compute_file_hash(f, "sha256")
        expected = hashlib.sha256(content).hexdigest()
        assert result == expected

    def test_sha512(self, tmp_path):
        """SHA-512 hash matches hashlib reference."""
        f = tmp_path / "test.bin"
        content = b"test data for sha512"
        f.write_bytes(content)
        result = compute_file_hash(f, "sha512")
        expected = hashlib.sha512(content).hexdigest()
        assert result == expected

    def test_empty_file(self, tmp_path):
        """Hash of empty file matches hashlib reference."""
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        result = compute_file_hash(f, "sha256")
        expected = hashlib.sha256(b"").hexdigest()
        assert result == expected

    def test_different_content_different_hash(self, tmp_path):
        """Different file content produces different hashes."""
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"aaa")
        f2.write_bytes(b"bbb")
        assert compute_file_hash(f1, "sha256") != compute_file_hash(f2, "sha256")

    def test_large_file(self, tmp_path):
        """Hash works for files larger than the internal chunk size (8192)."""
        f = tmp_path / "large.bin"
        content = b"x" * 20000
        f.write_bytes(content)
        result = compute_file_hash(f, "sha256")
        expected = hashlib.sha256(content).hexdigest()
        assert result == expected


# ===========================================================================
# filter_input_files
# ===========================================================================

def _file(type_: str = "file", source_module: str | None = None,
          source_module_type: str | None = None, name: str = "f") -> dict:
    """Create a minimal file_data dict for filter tests."""
    d = {"type": type_, "filename": name}
    if source_module:
        d["source_module"] = source_module
    if source_module_type:
        d["source_module_type"] = source_module_type
    return d


class TestFilterInputFiles:

    def test_none_returns_all(self):
        """input_types=None returns all files unfiltered."""
        files = [_file("file"), _file("sig"), _file("module_entry")]
        assert filter_input_files(files, None) == files

    def test_file_matches_file(self):
        """'file' matches type=file."""
        files = [_file("file")]
        assert len(filter_input_files(files, ["file"])) == 1

    def test_file_matches_project(self):
        """'file' matches type=project (group key)."""
        files = [_file("project")]
        assert len(filter_input_files(files, ["file"])) == 1

    def test_file_matches_manifest(self):
        """'file' matches type=manifest (group key)."""
        files = [_file("manifest")]
        assert len(filter_input_files(files, ["file"])) == 1

    def test_file_excludes_sig(self):
        """'file' does NOT match type=sig."""
        files = [_file("sig")]
        assert len(filter_input_files(files, ["file"])) == 0

    def test_file_excludes_module_entry(self):
        """'file' does NOT match type=module_entry."""
        files = [_file("module_entry")]
        assert len(filter_input_files(files, ["file"])) == 0

    def test_exact_type_project(self):
        """'project' matches only type=project."""
        files = [_file("file"), _file("project"), _file("manifest")]
        result = filter_input_files(files, ["project"])
        assert len(result) == 1
        assert result[0]["type"] == "project"

    def test_exact_type_manifest(self):
        """'manifest' matches only type=manifest."""
        files = [_file("file"), _file("manifest")]
        result = filter_input_files(files, ["manifest"])
        assert len(result) == 1
        assert result[0]["type"] == "manifest"

    def test_exact_type_sig(self):
        """'sig' matches only type=sig."""
        files = [_file("file"), _file("sig")]
        result = filter_input_files(files, ["sig"])
        assert len(result) == 1
        assert result[0]["type"] == "sig"

    def test_source_module_match(self):
        """Module name matches source_module."""
        files = [
            _file("file"),
            _file("module_entry", source_module="ots_timestamp"),
        ]
        result = filter_input_files(files, ["ots_timestamp"])
        assert len(result) == 1
        assert result[0]["source_module"] == "ots_timestamp"

    def test_source_module_type_match(self):
        """'module.entry_type' matches source_module + source_module_type."""
        files = [
            _file("module_entry", source_module="ots", source_module_type="proof"),
            _file("module_entry", source_module="ots", source_module_type="header"),
        ]
        result = filter_input_files(files, ["ots.proof"])
        assert len(result) == 1
        assert result[0]["source_module_type"] == "proof"

    def test_multiple_input_types(self):
        """Multiple input_types combined with OR logic."""
        files = [
            _file("file", name="a"),
            _file("sig", name="b"),
            _file("module_entry", source_module="mod", name="c"),
        ]
        result = filter_input_files(files, ["sig", "mod"])
        assert len(result) == 2
        names = {f["filename"] for f in result}
        assert names == {"b", "c"}

    def test_empty_input_types_matches_nothing(self):
        """Empty list matches nothing."""
        files = [_file("file"), _file("sig")]
        assert filter_input_files(files, []) == []

    def test_no_duplicates(self):
        """A file matching multiple input_types is only included once."""
        files = [_file("project")]
        # "file" (group) and "project" (exact) both match type=project
        result = filter_input_files(files, ["file", "project"])
        assert len(result) == 1


# ===========================================================================
# run_module_job_files
# ===========================================================================

class TestRunModuleJobFiles:

    def _make_input(self, tmp_path, files, config=None):
        data = {
            "config": config or {"identity_hash_algo": "sha256"},
            "output_dir": str(tmp_path),
            "files": files,
        }
        return _write_input(tmp_path, data)

    def _run(self, tmp_path, files, handler, config=None):
        input_path = self._make_input(tmp_path, files, config)
        args = argparse.Namespace(input=input_path)
        output = _capture_stdout(run_module_job_files, args, handler)
        events = _parse_ndjson(output)
        result = next((e for e in events if e.get("type") == "result"), None)
        non_result = [e for e in events if e.get("type") != "result"]
        return non_result, result

    def test_all_complete(self, tmp_path):
        """All files returning 'complete' produces overall 'complete'."""
        files = [
            {"file_path": "/tmp/a.txt", "config_key": "a", "hashes": {}, "module_config": {}},
            {"file_path": "/tmp/b.txt", "config_key": "b", "hashes": {}, "module_config": {}},
        ]
        _, result = self._run(tmp_path, files, handler=lambda f: {"status": "complete"})
        assert result["status"] == "complete"
        assert len(result["files"]) == 2

    def test_one_pending(self, tmp_path):
        """One file returning 'pending' makes overall 'pending'."""
        files = [
            {"file_path": "/tmp/a.txt", "config_key": "a", "hashes": {}, "module_config": {}},
            {"file_path": "/tmp/b.txt", "config_key": "b", "hashes": {}, "module_config": {}},
        ]
        statuses = iter(["complete", "pending"])
        _, result = self._run(tmp_path, files,
                              handler=lambda f: {"status": next(statuses)})
        assert result["status"] == "pending"

    def test_one_error(self, tmp_path):
        """One file returning 'error' makes overall 'error'."""
        files = [
            {"file_path": "/tmp/a.txt", "config_key": "a", "hashes": {}, "module_config": {}},
        ]
        _, result = self._run(tmp_path, files, handler=lambda f: {"status": "error"})
        assert result["status"] == "error"

    def test_error_takes_priority_over_pending(self, tmp_path):
        """Error status takes priority over pending."""
        files = [
            {"file_path": "/tmp/a.txt", "config_key": "a", "hashes": {}, "module_config": {}},
            {"file_path": "/tmp/b.txt", "config_key": "b", "hashes": {}, "module_config": {}},
            {"file_path": "/tmp/c.txt", "config_key": "c", "hashes": {}, "module_config": {}},
        ]
        statuses = iter(["pending", "error", "complete"])
        _, result = self._run(tmp_path, files,
                              handler=lambda f: {"status": next(statuses)})
        assert result["status"] == "error"

    def test_handler_returning_none_skipped(self, tmp_path):
        """Handler returning None is not included in result files."""
        files = [
            {"file_path": "/tmp/a.txt", "config_key": "a", "hashes": {}, "module_config": {}},
            {"file_path": "/tmp/b.txt", "config_key": "b", "hashes": {}, "module_config": {}},
        ]
        call_count = [0]

        def handler(f):
            call_count[0] += 1
            if f["config_key"] == "a":
                return None  # skip
            return {"status": "complete"}

        _, result = self._run(tmp_path, files, handler)
        assert call_count[0] == 2  # handler called for both
        assert len(result["files"]) == 1  # only one in results
        assert result["status"] == "complete"

    def test_empty_files(self, tmp_path):
        """No files produces overall 'complete' with empty files list."""
        _, result = self._run(tmp_path, [], handler=lambda f: {"status": "complete"})
        assert result["status"] == "complete"
        assert result["files"] == []

    def test_file_data_structure(self, tmp_path):
        """Handler receives correctly structured file_data."""
        files = [{
            "file_path": "/tmp/test.pdf",
            "config_key": "paper",
            "hashes": {"sha256": {"value": "abc"}},
            "module_config": {"calendars": ["http://cal"]},
        }]
        captured = []

        def handler(f):
            captured.append(f)
            return {"status": "complete"}

        self._run(tmp_path, files, handler,
                  config={"identity_hash_algo": "sha256"})

        fd = captured[0]
        assert fd["file_path"] == Path("/tmp/test.pdf")
        assert fd["filename"] == "test.pdf"
        assert fd["config_key"] == "paper"
        assert fd["hashes"] == {"sha256": {"value": "abc"}}
        assert fd["module_config"] == {"calendars": ["http://cal"]}
        assert fd["output_dir"] == Path(str(tmp_path))
        assert fd["identity_hash_algo"] == "sha256"
        assert fd["config"] == {"identity_hash_algo": "sha256"}


# ===========================================================================
# run_module_files
# ===========================================================================

class TestRunModuleFiles:

    def _make_input(self, tmp_path, files, config=None):
        data = {
            "config": config or {"identity_hash_algo": "sha256"},
            "output_dir": str(tmp_path),
            "files": files,
        }
        return _write_input(tmp_path, data)

    def _run(self, tmp_path, files, handler, config=None,
             post_parse=None, result_extra=None):
        input_path = self._make_input(tmp_path, files, config)
        args = argparse.Namespace(input=input_path)
        output = _capture_stdout(
            run_module_files, args, handler,
            post_parse=post_parse, result_extra=result_extra,
        )
        events = _parse_ndjson(output)
        result = next((e for e in events if e.get("type") == "result"), None)
        non_result = [e for e in events if e.get("type") != "result"]
        return non_result, result

    def test_basic_handler(self, tmp_path):
        """Handler is called for each file, result files collected."""
        files = [
            {"file_path": "/tmp/a.pdf", "config_key": "paper", "hashes": {},
             "module_config": {}},
            {"file_path": "/tmp/b.pdf", "config_key": "manifest", "hashes": {},
             "module_config": {}},
        ]

        def handler(f):
            return {"file_path": f["filename"], "config_key": f["config_key"]}

        _, result = self._run(tmp_path, files, handler)
        assert len(result["files"]) == 2
        assert result["files"][0]["file_path"] == "a.pdf"
        assert result["files"][1]["file_path"] == "b.pdf"

    def test_handler_returning_none(self, tmp_path):
        """Handler returning None skips the file in results."""
        files = [
            {"file_path": "/tmp/a.pdf", "config_key": "a", "hashes": {},
             "module_config": {}},
        ]
        _, result = self._run(tmp_path, files, handler=lambda f: None)
        assert result["files"] == []

    def test_post_parse_callback(self, tmp_path):
        """post_parse is called with parsed data before file iteration."""
        files = [
            {"file_path": "/tmp/a.pdf", "config_key": "a", "hashes": {},
             "module_config": {}},
        ]
        post_parse_called = [False]

        def post_parse(data):
            post_parse_called[0] = True
            assert "config" in data
            assert "files" in data
            return None  # no modification

        self._run(tmp_path, files, handler=lambda f: {"ok": True},
                  post_parse=post_parse)
        assert post_parse_called[0]

    def test_post_parse_can_modify_data(self, tmp_path):
        """post_parse returning modified data replaces original."""
        files = [
            {"file_path": "/tmp/a.pdf", "config_key": "a", "hashes": {},
             "module_config": {}},
            {"file_path": "/tmp/b.pdf", "config_key": "b", "hashes": {},
             "module_config": {}},
        ]

        def post_parse(data):
            # Remove second file
            data["files"] = data["files"][:1]
            return data

        _, result = self._run(tmp_path, files, handler=lambda f: {"ok": True},
                              post_parse=post_parse)
        assert len(result["files"]) == 1

    def test_result_extra_merged(self, tmp_path):
        """result_extra dict is merged into the result JSON."""
        files = [
            {"file_path": "/tmp/a.pdf", "config_key": "a", "hashes": {},
             "module_config": {}},
        ]
        job_desc = {"module": "ots_timestamp", "type": "upgrade"}
        _, result = self._run(tmp_path, files, handler=lambda f: {"ok": True},
                              result_extra={"job": job_desc})
        assert result["job"] == job_desc

    def test_file_data_structure(self, tmp_path):
        """Handler receives correctly structured file_data with all fields."""
        files = [{
            "file_path": "/tmp/test.pdf",
            "config_key": "paper",
            "type": "file",
            "hashes": {"sha256": {"value": "abc"}},
            "module_config": {"nonce": True},
            "source_module": "ots",
            "source_module_type": "proof",
        }]
        captured = []

        def handler(f):
            captured.append(f)
            return {"ok": True}

        self._run(tmp_path, files, handler,
                  config={"identity_hash_algo": "sha256"})

        fd = captured[0]
        assert fd["file_path"] == Path("/tmp/test.pdf")
        assert fd["filename"] == "test.pdf"
        assert fd["config_key"] == "paper"
        assert fd["type"] == "file"
        assert fd["hashes"] == {"sha256": {"value": "abc"}}
        assert fd["module_config"] == {"nonce": True}
        assert fd["output_dir"] == Path(str(tmp_path))
        assert fd["identity_hash_algo"] == "sha256"
        assert fd["source_module"] == "ots"
        assert fd["source_module_type"] == "proof"

    def test_input_types_filtering(self, tmp_path):
        """input_types in module_config filters files before handler."""
        files = [
            {"file_path": "/tmp/a.pdf", "config_key": "paper", "type": "file",
             "hashes": {}, "module_config": {"input_types": ["sig"]}},
            {"file_path": "/tmp/a.sig", "config_key": "paper", "type": "sig",
             "hashes": {}, "module_config": {"input_types": ["sig"]}},
        ]
        call_count = [0]

        def handler(f):
            call_count[0] += 1
            return {"file_path": f["filename"]}

        _, result = self._run(tmp_path, files, handler)
        assert call_count[0] == 1  # only sig file processed
        assert result["files"][0]["file_path"] == "a.sig"

    def test_input_types_none_processes_all(self, tmp_path):
        """No input_types in module_config processes all files."""
        files = [
            {"file_path": "/tmp/a.pdf", "config_key": "a", "type": "file",
             "hashes": {}, "module_config": {}},
            {"file_path": "/tmp/b.sig", "config_key": "b", "type": "sig",
             "hashes": {}, "module_config": {}},
        ]
        call_count = [0]

        def handler(f):
            call_count[0] += 1
            return {"ok": True}

        self._run(tmp_path, files, handler)
        assert call_count[0] == 2
