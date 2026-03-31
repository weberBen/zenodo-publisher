#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""Run tests for ZP built-in modules.

Each module lives in ``release_tool/modules/<name>/`` and has its own venv
managed by uv. This script detects modules automatically (any subdirectory
with a ``pyproject.toml``) and runs pytest inside the module's directory.

Usage
-----
  tests/run_module_tests.py                                          # list modules
  tests/run_module_tests.py --all                                    # run all modules
  tests/run_module_tests.py digicert_timestamp                                    # one module
  tests/run_module_tests.py digicert_timestamp.test_module                        # one file (root)
  tests/run_module_tests.py digicert_timestamp.tests.test_module                  # one file in subdir (/ → .)
  tests/run_module_tests.py "digicert_timestamp.tests.test_module::func"          # one function
  tests/run_module_tests.py "digicert_timestamp.tests.test_module::test_0[6-10]"

Extra pytest args can come after ``--`` or directly (unknown flags are forwarded):
  tests/run_module_tests.py digicert_timestamp -- -v -s -m "not network"
  tests/run_module_tests.py digicert_timestamp -v
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
import argcomplete

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
BUILTIN_MODULES_DIR = PROJECT_ROOT / "release_tool" / "modules"


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------


def _discover_modules() -> dict[str, Path]:
    """Return ``{module_name: module_dir}`` for all detectable built-in modules."""
    modules: dict[str, Path] = {}
    if not BUILTIN_MODULES_DIR.is_dir():
        return modules
    for candidate in sorted(BUILTIN_MODULES_DIR.iterdir()):
        if (
            candidate.is_dir()
            and not candidate.name.startswith((".", "_"))
            and (candidate / "pyproject.toml").exists()
        ):
            modules[candidate.name] = candidate
    return modules


def _discover_test_files(module_dir: Path) -> list[str]:
    """Return test file paths relative to module_dir (without ``.py``), via pytest collection."""
    ids = _collect_pytest_ids(module_dir)
    seen: set[str] = set()
    result: list[str] = []
    for node_id in ids:
        stem = node_id.split("::")[0].removesuffix(".py")
        if stem not in seen:
            seen.add(stem)
            result.append(stem)
    return sorted(result)


def _collect_pytest_ids(module_dir: Path, test_file: str | None = None) -> list[str]:
    """Delegate to ``pytest --collect-only`` to enumerate node IDs.

    This is the "pytest → script" direction: we do not re-implement test
    discovery; pytest does it and we just read its output.
    """
    cmd = [
        "uv", "run", "--project", str(module_dir),
        "pytest", "--collect-only", "-q", "--no-header",
    ]
    if test_file:
        cmd.append(test_file)
    _completion_vars = {"VIRTUAL_ENV", "_ARGCOMPLETE", "_ARGCOMPLETE_IFS", "_ARGCOMPLETE_SHELL",
                        "COMP_LINE", "COMP_POINT", "COMP_TYPE", "COMP_KEY", "COMP_WORDBREAKS"}
    env = {k: v for k, v in os.environ.items() if k not in _completion_vars}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(module_dir), env=env, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    ids: list[str] = []
    current_dir = ""
    current_module = ""
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        # Flat -q format: "path/file.py::test_name"
        if "::" in stripped and not stripped.startswith(("<", "=", "WARN", "ERROR", "no tests")):
            ids.append(stripped.split()[0])
            continue
        # Tree format: <Dir tests>, <Module file.py>, <Function name>
        m = re.match(r"<Dir\s+(.+?)>", stripped)
        if m:
            current_dir = m.group(1)
            continue
        m = re.match(r"<Module\s+(.+?)>", stripped)
        if m:
            current_module = (f"{current_dir}/{m.group(1)}" if current_dir else m.group(1))
            continue
        m = re.match(r"<Function\s+(.+?)>", stripped)
        if m and current_module:
            ids.append(f"{current_module}::{m.group(1)}")
    return ids


# ---------------------------------------------------------------------------
# Argcomplete completer
# ---------------------------------------------------------------------------


class _ModuleTestCompleter:
    """Complete ``module[.testfile[::func_pattern]]`` arguments.

    * No ``.``  → complete module names
    * ``mod.``  → complete test file stems in that module
    * ``mod.file::`` → call pytest --collect-only and return function names
    """

    def __call__(self, prefix: str, **kwargs) -> list[str]:
        modules = _discover_modules()

        if "::" in prefix:
            node_prefix, func_prefix = prefix.split("::", 1)
            if "." not in node_prefix:
                return []
            module_name, file_stem = node_prefix.split(".", 1)
            module_dir = modules.get(module_name)
            if not module_dir:
                return []
            ids = _collect_pytest_ids(module_dir, file_stem.replace(".", "/") + ".py")
            completions: list[str] = []
            for node_id in ids:
                if "::" not in node_id:
                    continue
                func_part = node_id.split("::", 1)[1]
                if func_part.startswith(func_prefix):
                    completions.append(f"{node_prefix}::{func_part}")
            return completions

        if "." in prefix:
            module_name, file_prefix = prefix.split(".", 1)
            module_dir = modules.get(module_name)
            if not module_dir:
                return []
            files = _discover_test_files(module_dir)
            return [
                f"{module_name}.{f.replace('/', '.')}"
                for f in files
                if f.replace("/", ".").startswith(file_prefix)
            ]

        return [name for name in modules if name.startswith(prefix)]


# ---------------------------------------------------------------------------
# Argument parsing helpers
# ---------------------------------------------------------------------------


def _parse_target(target: str) -> tuple[str, str | None, str | None]:
    """Split ``module[.testfile[::func_pattern]]`` into its components."""
    func_pattern: str | None = None
    if "::" in target:
        node_id, func_pattern = target.split("::", 1)
    else:
        node_id = target
    parts = node_id.split(".", 1)
    module_name = parts[0]
    file_stem = parts[1] if len(parts) > 1 else None
    return module_name, file_stem, func_pattern


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _run_module(
    module_name: str,
    module_dir: Path,
    file_stem: str | None,
    func_pattern: str | None,
    extra: list[str],
) -> int:
    """Run pytest for one module. Returns the pytest exit code."""
    cmd = ["uv", "run", "--project", str(module_dir), "pytest"]

    if file_stem:
        node = f"{file_stem.replace('.', '/')}.py"
        if func_pattern:
            node += f"::{func_pattern}"
        cmd.append(node)

    cmd.extend(extra)

    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}

    print(f"\n{'─' * 60}", flush=True)
    print(f"  module : {module_name}", flush=True)
    print(f"  cwd    : {module_dir}", flush=True)
    print(f"  cmd    : {' '.join(cmd)}", flush=True)
    print(f"{'─' * 60}", flush=True)

    return subprocess.run(cmd, cwd=str(module_dir), env=env).returncode


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    # Split at '--': everything after is forwarded verbatim to pytest.
    argv = sys.argv[1:]
    if "--" in argv:
        split = argv.index("--")
        script_argv = argv[:split]
        extra_after_sep = argv[split + 1:]
    else:
        script_argv = argv
        extra_after_sep = []

    parser = argparse.ArgumentParser(
        prog="run_module_tests.py",
        description="Run ZP module tests.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all modules.",
    )
    target_action = parser.add_argument(
        "target",
        nargs="?",
        default=None,
        metavar="module[.testfile[::func_pattern]]",
        help=(
            "Target module, file, or function. "
            "Examples: digicert_timestamp  "
            "digicert_timestamp.test_module_digicert_timestamp::  "
            "digicert_timestamp.test_module_digicert_timestamp::test_func"
        ),
    )

    target_action.completer = _ModuleTestCompleter()  # type: ignore[attr-defined]
    argcomplete.autocomplete(parser)

    # parse_known_args so that unknown flags (e.g. -v before --) go to pytest
    args, extra_unknown = parser.parse_known_args(script_argv)
    extra_args = extra_unknown + extra_after_sep

    modules = _discover_modules()
    if not modules:
        print("No modules found.", file=sys.stderr)
        return 1

    if args.target is None:
        if not args.all:
            print("Available modules:")
            for name in modules:
                print(f"  {name}")
            print(f"\nRun all:  {parser.prog} --all")
            print(f"Run one:  {parser.prog} <module>[.testfile[::func]]")
            return 0
        targets = [(name, path, None, None) for name, path in modules.items()]
    else:
        module_name, file_stem, func_pattern = _parse_target(args.target)
        if module_name not in modules:
            avail = ", ".join(modules)
            print(
                f"Module '{module_name}' not found. Available: {avail}",
                file=sys.stderr,
            )
            return 1
        targets = [(module_name, modules[module_name], file_stem, func_pattern)]

    overall = 0
    for name, path, file_stem, func_pattern in targets:
        rc = _run_module(name, path, file_stem, func_pattern, extra_args)
        if rc != 0:
            overall = rc

    return overall


if __name__ == "__main__":
    # use argcomplete.warn("my value", value) to display log durint completion
    sys.exit(main())