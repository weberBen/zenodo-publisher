"""Module loader for zenodo-publisher pipeline modules.

Built-in modules live in release_tool/modules/<name>/main.py.
User modules live in ~/.zenodo/modules/<name>/main.py or ~/.zenodo/modules/<name>.py.
"""

import json
import subprocess
import tempfile
from pathlib import Path

from ..errors import ZPError


class ModuleError(ZPError):
    """Error raised by a pipeline module."""
    _prefix = "module"


def find_module_path(provider_name: str) -> Path:
    """Return path to module script. Raises ModuleError if not found."""
    # 1. Built-in: release_tool/modules/<name>/main.py
    builtin = Path(__file__).parent / provider_name / "main.py"
    if builtin.exists():
        return builtin

    # 2. User directory: ~/.zenodo/modules/<name>/main.py
    user_dir = Path.home() / ".zenodo" / "modules" / provider_name / "main.py"
    if user_dir.exists():
        return user_dir

    # 3. User single file: ~/.zenodo/modules/<name>.py
    user_file = Path.home() / ".zenodo" / "modules" / f"{provider_name}.py"
    if user_file.exists():
        return user_file

    raise ModuleError(
        f"Module '{provider_name}' not found. "
        f"Looked in built-ins and {Path.home() / '.zenodo' / 'modules'}.",
        name="not_found",
    )


def load_module(provider_name: str) -> Path:
    """Validate module exists at config load time. Returns its path."""
    return find_module_path(provider_name)


def is_builtin(provider_name: str) -> bool:
    """Return True if the module is a built-in ZP module."""
    return (Path(__file__).parent / provider_name / "main.py").exists()


def run_module(provider_name: str, input_data: dict, output_module) -> list[dict]:
    """Run a module as a subprocess via uv.

    Passes input_data as JSON file, reads NDJSON output.
    Events are relayed to output_module.emit().
    Returns the list of file dicts from the 'result' event.
    """
    module_path = find_module_path(provider_name)

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
