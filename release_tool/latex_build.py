"""Compilation utilities."""

import os
import subprocess
from pathlib import Path

from . import output
from .subprocess_utils import run as run_cmd


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
        raise FileNotFoundError(f"Makefile not found at {makefile}")

    output.info("Building document in {compile_dir}...", compile_dir=str(compile_dir), name="compile_start")

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
        output.info_ok("Compilation successful", name="compile_ok")

    except subprocess.CalledProcessError as e:
        output.error("Compilation failed", name="compile_error")
        if e.stdout:
            output.detail("Stdout:\n{stdout}", stdout=e.stdout, name="compile_stdout")
        if e.stderr:
            output.detail("Stderr:\n{stderr}", stderr=e.stderr, name="compile_stderr")
        raise RuntimeError("Compilation failed") from e
