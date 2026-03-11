"""ZP CLI runner for E2E tests.

Calls `uv run --project <zp_root> zp` via subprocess.
Completely independent from release_tool internals.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .ndjson import parse_stream

# Root of the zenodo-publisher project (resolved from this file's location)
ZP_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class ZpResult:
    returncode: int
    stdout: str
    stderr: str
    events: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class ZpRunner:
    """Subprocess wrapper for `uv run zp`."""

    def __init__(self, work_dir: Path, uv_binary: str = "uv"):
        self.work_dir = work_dir
        self.uv_binary = uv_binary

    def run(self, *args: str, timeout: int = 120) -> ZpResult:
        """Run zp with given arguments."""
        cmd = [
            self.uv_binary, "run",
            "--project", str(ZP_PROJECT_ROOT),
            "zp",
        ] + list(args)

        result = subprocess.run(
            cmd,
            cwd=str(self.work_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        events = parse_stream(result.stdout) if "--test-mode" in args else []

        return ZpResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            events=events,
        )

    def run_with_config(self, command: str, cli_args: list[str],
                        test_config_path: Path | None = None,
                        timeout: int = 120) -> ZpResult:
        """Run zp with a test config file.

        Args:
            command: Subcommand ("release", "archive").
            cli_args: Additional CLI arguments.
            test_config_path: Path to test.config.yaml (enables --test-mode).
            timeout: Subprocess timeout in seconds.
        """
        args = [command, "--test-mode"]
        if test_config_path:
            args.extend(["--test-config", str(test_config_path)])
        args.extend(cli_args)
        return self.run(*args, timeout=timeout)

    def archive(self, tag: str, test_mode: bool = True,
                test_config: Path | None = None, **kwargs) -> ZpResult:
        """Run `zp archive --tag <tag>` with optional extra flags."""
        args = ["archive", "--tag", tag]
        if test_mode:
            args.append("--test-mode")
        if test_config:
            args.extend(["--test-config", str(test_config)])
        for k, v in kwargs.items():
            flag = f"--{k.replace('_', '-')}"
            if v is True:
                args.append(flag)
            elif v is not False and v is not None:
                args.extend([flag, str(v)])
        return self.run(*args)

    def release(self, test_mode: bool = True,
                test_config: Path | None = None, **kwargs) -> ZpResult:
        """Run `zp release` with optional extra flags."""
        args = ["release"]
        if test_mode:
            args.append("--test-mode")
        if test_config:
            args.extend(["--test-config", str(test_config)])
        for k, v in kwargs.items():
            flag = f"--{k.replace('_', '-')}"
            if v is True:
                args.append(flag)
            elif v is not False and v is not None:
                args.extend([flag, str(v)])
        return self.run(*args)
