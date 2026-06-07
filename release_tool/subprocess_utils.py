"""Subprocess wrapper with debug logging."""

import subprocess
from . import output


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess command with debug logging.

    Logs the command via output.cmd() before executing.
    Logs the result as NDJSON data.
    All kwargs are forwarded to subprocess.run().
    """
    output.cmd(args)
    result = subprocess.run(args, **kwargs)

    output.data("subprocess_result", {
        "cmd": args,
        "returncode": result.returncode,
        "stdout": result.stdout if isinstance(result.stdout, str) else None,
        "stderr": result.stderr if isinstance(result.stderr, str) else None,
    })

    return result
