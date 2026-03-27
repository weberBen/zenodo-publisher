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

    output.info(f"📄 Building document in {compile_dir}...")

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
        output.info_ok("Compilation successful")

    except subprocess.CalledProcessError as e:
        output.error("Compilation failed")
        if e.stdout:
            output.detail(f"Stdout:\n{e.stdout}")
        if e.stderr:
            output.detail(f"Stderr:\n{e.stderr}")
        raise RuntimeError("Compilation failed") from e
