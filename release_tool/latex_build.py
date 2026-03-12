"""Compilation utilities."""

import os
import subprocess
from pathlib import Path

from . import output
from .subprocess_utils import run as run_cmd
from .errors import CompileError


def compile(compile_dir: Path, make_args: list[str] | None = None, env_vars: dict | None = None) -> None:
    """
    Compile document using Makefile.

    Args:
        compile_dir: Path to compile directory
        make_args: Extra arguments passed to make

    Raises:
        RuntimeError: If compilation fails
        FileNotFoundError: If Makefile doesn't exist
    """
    makefile = compile_dir / "Makefile"

    if not makefile.exists():
        raise CompileError(f"Makefile not found at {makefile}", name="makefile.not_found")

    output.info("Building document in {compile_dir}...", compile_dir=str(compile_dir), name="start")

    env = {**os.environ, **env_vars} if env_vars else None

    cmd = ["make", "deploy"] + (make_args or [])
    try:
        run_cmd(
            cmd,
            cwd=compile_dir,
            check=True,
            text=True,
            env=env,
        )
        output.info_ok("Compilation successful", name="ok")

    except subprocess.CalledProcessError as e:
        output.error("Compilation failed", name="error")
        if e.stdout:
            output.detail("Stdout:\n{stdout}", stdout=e.stdout, name="stdout")
        if e.stderr:
            output.detail("Stderr:\n{stderr}", stderr=e.stderr, name="stderr")
        raise CompileError("Compilation failed", name="failed") from e
