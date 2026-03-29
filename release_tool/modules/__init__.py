"""Module loader for zenodo-publisher pipeline modules.

Modules are directories containing main.py and pyproject.toml (uv project).

Lookup order (first match wins):
  1. Built-in:     release_tool/modules/<name>/main.py
  2. Project root: <project_root>/.zp/modules/<name>/main.py
  3. User home:    ~/.zp/modules/<name>/main.py

Execution: uv run --project <module_dir> main.py
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from ..errors import ZPError


class ModuleError(ZPError):
    """Error raised by a pipeline module."""
    _prefix = "module"


def _build_uv_cmd(module_path: Path, *args) -> list[str]:
    """Build the uv command for running a module via its project directory."""
    return ["uv", "run", "--project", str(module_path.parent), str(module_path), *args]


def _subprocess_env() -> dict:
    """Return os.environ without VIRTUAL_ENV so uv doesn't warn about env mismatch."""
    return {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}


def find_module_path(provider_name: str, project_root: Path | None = None) -> Path:
    """Return path to module main.py. Raises ModuleError if not found.

    Each module must be a directory with main.py and pyproject.toml.
    """
    # 1. Built-in: release_tool/modules/<name>/main.py
    builtin = Path(__file__).parent / provider_name / "main.py"
    if builtin.exists():
        return builtin

    # 2. Project root: <project_root>/.zp/modules/<name>/main.py
    if project_root is not None:
        proj = project_root / ".zp" / "modules" / provider_name / "main.py"
        if proj.exists():
            return proj

    # 3. User home: ~/.zp/modules/<name>/main.py
    user = Path.home() / ".zp" / "modules" / provider_name / "main.py"
    if user.exists():
        return user

    raise ModuleError(
        f"Module '{provider_name}' not found. "
        f"Looked in built-ins, project .zp/modules/, and {Path.home() / '.zp' / 'modules'}.",
        name="not_found",
    )


def load_module(provider_name: str, project_root: Path | None = None) -> Path:
    """Validate module exists at config load time. Returns its path."""
    return find_module_path(provider_name, project_root=project_root)


def is_builtin(provider_name: str) -> bool:
    """Return True if the module is a built-in ZP module."""
    return (Path(__file__).parent / provider_name / "main.py").exists()


def check_module(provider_name: str, module_config: dict, output_module,
                 project_root: Path | None = None) -> None:
    """Run module --check mode. Raises ModuleError if check fails.

    Passes module_config as JSON file via --config.
    Relays NDJSON events to output_module.
    """
    module_path = find_module_path(provider_name, project_root=project_root)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump({"module_config": module_config}, f)
        config_path = f.name

    try:
        proc = subprocess.run(
            _build_uv_cmd(module_path, "--check", "--config", config_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_subprocess_env(),
        )
    finally:
        Path(config_path).unlink(missing_ok=True)

    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        output_module.emit(event)

    stderr = proc.stderr.strip()
    if proc.returncode != 0:
        detail = f"\n{stderr}" if stderr else ""
        raise ModuleError(
            f"Module '{provider_name}' check failed (exit code {proc.returncode}){detail}",
            name="check_failed",
        )
    if stderr:
        output_module.emit({
            "type": "warn",
            "msg": stderr,
            "name": "module.stderr",
            "data": {"module_name": provider_name},
        })


def run_module(provider_name: str, input_data: dict, output_module,
               project_root: Path | None = None) -> list[dict]:
    """Run a module as a subprocess via uv.

    Passes input_data as JSON file, reads NDJSON output.
    Events are relayed to output_module.emit().
    Returns the list of file dicts from the 'result' event.
    """
    module_path = find_module_path(provider_name, project_root=project_root)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(input_data, f)
        input_path = f.name

    try:
        proc = subprocess.run(
            _build_uv_cmd(module_path, "--input", input_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_subprocess_env(),
        )
    finally:
        Path(input_path).unlink(missing_ok=True)

    result_files = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            result_files = event.get("files", [])
        else:
            output_module.emit(event)

    stderr = proc.stderr.strip()
    if proc.returncode != 0:
        detail = f"\n{stderr}" if stderr else ""
        raise ModuleError(
            f"Module '{provider_name}' exited with code {proc.returncode}{detail}",
            name="run_error",
        )
    if stderr:
        output_module.emit({
            "type": "warn",
            "msg": stderr,
            "name": "module.stderr",
            "data": {"module_name": provider_name},
        })

    return result_files
