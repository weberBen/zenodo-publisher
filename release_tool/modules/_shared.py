"""Shared utilities for built-in ZP modules.

Built-in modules can import from this file via:
    from _shared import create_emitter, compute_file_hash

This works because ZP adds the modules/ directory to PYTHONPATH
when running built-in modules. Custom modules cannot use this —
they must implement their own emit/hash functions.
"""

import hashlib
import json
from pathlib import Path


def create_emitter(module_name: str):
    """Create an emit function prefixed with the module name.

    Usage:
        emit = create_emitter("digicert_timestamp")
        emit("detail", "Hello", name="start")
        # → {"type": "detail", "msg": "Hello", "name": "digicert_timestamp.start"}
    """
    def emit(type_: str, msg: str, name: str = "", **kwargs) -> None:
        event = {
            "type": type_,
            "msg": msg,
            "name": f"{module_name}.{name}" if name else "",
        }
        if kwargs:
            event["data"] = kwargs
        print(json.dumps(event), flush=True)
    return emit


def compute_file_hash(file_path: Path, algo: str) -> str:
    """Compute hash of a file and return hex digest."""
    h = hashlib.new(algo)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def filter_input_files(files_data: list[dict], input_types: list[str] | None) -> list[dict]:
    """Filter file_data dicts by input_types.

    input_types entries:
      "file" / "project" / "manifest" → matches by type
      "sig"                           → matches SIG type
      "<module_name>"                 → matches MODULE_ENTRY from that module
      "<module_name>.<entry_type>"    → matches specific module sub-type

    If input_types is None, returns all files (no filtering).
    """
    if input_types is None:
        return files_data

    result = []
    for f in files_data:
        file_type = f.get("type")
        source_module = f.get("source_module")
        source_module_type = f.get("source_module_type")

        for t in input_types:
            if t == "file" and file_type not in ("sig", "module_entry"):
                result.append(f)
                break
            if t in ("project", "manifest", "sig") and file_type == t:
                result.append(f)
                break
            if source_module and source_module == t:
                result.append(f)
                break
            if source_module and source_module_type and f"{source_module}.{source_module_type}" == t:
                result.append(f)
                break
    return result


def run_module_files(args, handler, post_parse=None):
    """Parse module input JSON, iterate over files, collect results.

    Args:
        args: CLI args with args.input pointing to the JSON input file.
        handler: callback(file_data) -> dict|None. Called for each file.
            Returns a result entry dict or None to skip.
        post_parse: optional callback(data) called after JSON parsing,
            before file iteration. Use for validation (e.g. check algo support).

    file_data keys:
        file_path, filename, config_key, type, hashes, module_config,
        output_dir, identity_hash_algo, config
    """
    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    config = data["config"]
    identity_hash_algo = config.get("identity_hash_algo", None)
    output_dir = Path(data["output_dir"])

    if post_parse:
        data = post_parse(data) or data

    # Build file_data list
    all_files = []
    for file_info in data["files"]:
        file_path = Path(file_info["file_path"])
        all_files.append({
            "file_path": file_path,
            "filename": file_path.name,
            "config_key": file_info["config_key"],
            "type": file_info.get("type"),
            "hashes": file_info.get("hashes", {}),
            "module_config": file_info.get("module_config", {}),
            "output_dir": output_dir,
            "identity_hash_algo": identity_hash_algo,
            "config": config,
            "data": data,
            "source_module": file_info.get("source_module"),
            "source_module_type": file_info.get("source_module_type"),
        })

    # Filter by input_types if present in any file's module_config
    sample_cfg = all_files[0]["module_config"] if all_files else {}
    input_types = sample_cfg.get("input_types", None)
    files_to_process = filter_input_files(all_files, input_types)

    result_files = []
    for file_data in files_to_process:
        result = handler(file_data)
        if result:
            result_files.append(result)

    print(json.dumps({"type": "result", "files": result_files}), flush=True)

