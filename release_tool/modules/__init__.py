"""Module loader for zenodo-publisher pipeline modules.

Modules are directories containing <name>.py and pyproject.toml (uv project).
The entry point filename must match the module directory name.

Lookup order (first match wins):
  1. Built-in:     release_tool/modules/<name>/<name>.py
  2. Project root: <project_root>/.zp/modules/<name>/<name>.py
  3. User home:    ~/.zp/modules/<name>/<name>.py

Execution: uv run --project <module_dir> <name>.py
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from ..errors import ZPError

_VALID_MODULE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _sanitize_module_name(name: str) -> str:
    """Validate that a module name is safe for filesystem lookup."""
    if not _VALID_MODULE_NAME.match(name):
        raise ModuleError(
            f"Invalid module name '{name}'. "
            "Must be lowercase alphanumeric + underscores, "
            "start with a letter, max 64 chars.",
            name="invalid_name",
        )
    return name


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
    """Return path to module entry point. Raises ModuleError if not found.

    Each module must be a directory with <name>.py and pyproject.toml.
    """
    _sanitize_module_name(provider_name)

    entry = f"{provider_name}.py"

    # 1. Built-in: release_tool/modules/<name>/<name>.py
    builtin = Path(__file__).parent / provider_name / entry
    if builtin.exists():
        return builtin

    # 2. Project root: <project_root>/.zp/modules/<name>/<name>.py
    if project_root is not None:
        proj = project_root / ".zp" / "modules" / provider_name / entry
        if proj.exists():
            return proj

    # 3. User home: ~/.zp/modules/<name>/<name>.py
    user = Path.home() / ".zp" / "modules" / provider_name / entry
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
    return (Path(__file__).parent / provider_name / f"{provider_name}.py").exists()


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
            _build_uv_cmd(module_path, "check", "--config", config_path),
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
        output_module.module_emit(event, module_name=provider_name)

    stderr = proc.stderr.strip()
    if proc.returncode != 0:
        detail = f"\n{stderr}" if stderr else ""
        raise ModuleError(
            f"Module '{provider_name}' check failed (exit code {proc.returncode}){detail}",
            name="check_failed",
        )
    if stderr:
        output_module.module_emit({
            "type": "warn",
            "msg": stderr,
            "name": "module.stderr",
        }, module_name=provider_name)


def run_module(provider_name: str, input_data: dict, output_module,
               project_root: Path | None = None) -> list[dict]:
    """Run a module as a subprocess via uv.

    Passes input_data as JSON file, reads NDJSON output.
    Events are relayed to output_module.module_emit().
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
            _build_uv_cmd(module_path, "run", "--input", input_path),
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
            output_module.module_emit(event, module_name=provider_name)

    stderr = proc.stderr.strip()
    if proc.returncode != 0:
        detail = f"\n{stderr}" if stderr else ""
        raise ModuleError(
            f"Module '{provider_name}' exited with code {proc.returncode}{detail}",
            name="run_error",
        )
    if stderr:
        output_module.module_emit({
            "type": "warn",
            "msg": stderr,
            "name": "module.stderr",
        }, module_name=provider_name)

    return result_files


def run_module_standalone(provider_name: str, args: list[str],
                          project_root: Path | None = None,
                          output_module=None) -> int:
    """Run a module in standalone mode, passing args directly to the module subprocess.

    When output_module is provided, stdout is captured and NDJSON events are
    relayed through output_module.module_emit(). Non-NDJSON lines are printed
    as-is. When output_module is None, stdout passes through directly.

    Returns the subprocess exit code.
    """
    module_path = find_module_path(provider_name, project_root=project_root)
    cmd = _build_uv_cmd(module_path, *args)

    if output_module is None:
        proc = subprocess.run(cmd, env=_subprocess_env())
        return proc.returncode

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=_subprocess_env(),
    )
    for line in proc.stdout:
        line = line.rstrip("\n")
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            print(line)
            continue
        output_module.module_emit(event, module_name=provider_name)

    proc.wait()
    stderr = proc.stderr.read().strip()
    if stderr:
        output_module.module_emit(
            {"type": "warn", "msg": stderr, "name": "module.stderr"},
            module_name=provider_name,
        )
    return proc.returncode


def list_modules(project_root: Path | None = None) -> dict[str, tuple[str, Path]]:
    """List all available modules with their source and path.

    Returns dict of {name: (source, module_dir)} where source is
    "built-in", "project", or "user".
    """
    modules = {}

    # 1. Built-in
    builtin_dir = Path(__file__).parent
    for entry in sorted(builtin_dir.iterdir()):
        if entry.is_dir() and (entry / f"{entry.name}.py").exists():
            modules[entry.name] = ("built-in", entry)

    # 2. Project root
    if project_root is not None:
        proj_dir = project_root / ".zp" / "modules"
        if proj_dir.exists():
            for entry in sorted(proj_dir.iterdir()):
                if entry.is_dir() and (entry / f"{entry.name}.py").exists():
                    if entry.name not in modules:
                        modules[entry.name] = ("project", entry)

    # 3. User home
    user_dir = Path.home() / ".zp" / "modules"
    if user_dir.exists():
        for entry in sorted(user_dir.iterdir()):
            if entry.is_dir() and (entry / f"{entry.name}.py").exists():
                if entry.name not in modules:
                    modules[entry.name] = ("user", entry)

    return modules
