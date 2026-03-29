"""Module loader for zenodo-publisher pipeline modules.

Lookup order (first match wins):
  1. Built-in:     release_tool/modules/<name>/main.py
  2. Project root: <project_root>/.zp/modules/<name>/main.py
                   <project_root>/.zp/modules/<name>.py
  3. User home:    ~/.zp/modules/<name>/main.py
                   ~/.zp/modules/<name>.py
"""

import json
import subprocess
import tempfile
from pathlib import Path

from ..errors import ZPError


class ModuleError(ZPError):
    """Error raised by a pipeline module."""
    _prefix = "module"


def find_module_path(provider_name: str, project_root: Path | None = None) -> Path:
    """Return path to module script. Raises ModuleError if not found."""
    # 1. Built-in: release_tool/modules/<name>/main.py
    builtin = Path(__file__).parent / provider_name / "main.py"
    if builtin.exists():
        return builtin

    # 2. Project root: <project_root>/.zp/modules/<name>/main.py or <name>.py
    if project_root is not None:
        proj_dir = project_root / ".zp" / "modules" / provider_name / "main.py"
        if proj_dir.exists():
            return proj_dir
        proj_file = project_root / ".zp" / "modules" / f"{provider_name}.py"
        if proj_file.exists():
            return proj_file

    # 3. User home: ~/.zp/modules/<name>/main.py or <name>.py
    user_dir = Path.home() / ".zp" / "modules" / provider_name / "main.py"
    if user_dir.exists():
        return user_dir
    user_file = Path.home() / ".zp" / "modules" / f"{provider_name}.py"
    if user_file.exists():
        return user_file

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
            ["uv", "run", str(module_path), "--check", "--config", config_path],
            stdout=subprocess.PIPE,
            text=True,
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

    if proc.returncode != 0:
        raise ModuleError(
            f"Module '{provider_name}' check failed (exit code {proc.returncode})",
            name="check_failed",
        )


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
            ["uv", "run", str(module_path), "--input", input_path],
            stdout=subprocess.PIPE,
            text=True,
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

    if proc.returncode != 0:
        raise ModuleError(
            f"Module '{provider_name}' exited with code {proc.returncode}",
            name="run_error",
        )

    return result_files
