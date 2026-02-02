"""Compilation utilities."""

import subprocess
from pathlib import Path


def compile(compile_dir: Path) -> None:
    """
    Compile document using Makefile.

    Args:
        compile_dir: Path to compile directory

    Raises:
        RuntimeError: If compilation fails
        FileNotFoundError: If Makefile doesn't exist
    """
    makefile = compile_dir / "Makefile"

    if not makefile.exists():
        raise FileNotFoundError(f"Makefile not found at {makefile}")

    print(f"📄 Building document in {compile_dir}...\n\n")

    try:
        result = subprocess.run(
            ["make", "deploy"],
            cwd=compile_dir,
            check=True,
            # capture_output=True,
            text=True
        )
        print("\n\n✅ Compilation successful")

    except subprocess.CalledProcessError as e:
        print(f"✗ Compilation failed")
        print(f"\nStdout:\n{e.stdout}")
        print(f"\nStderr:\n{e.stderr}")
        raise RuntimeError("Compilation failed") from e
