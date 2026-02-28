"""Subprocess wrapper with debug logging."""

import subprocess
from . import output


def run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess command with debug logging.

    Logs the command via output.cmd() before executing.
    All kwargs are forwarded to subprocess.run().
    """
    output.cmd(args)
    return subprocess.run(args, **kwargs)
