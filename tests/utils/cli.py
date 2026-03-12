"""ZP CLI runner for E2E tests.

Calls `uv run --project <zp_root> zp` via subprocess.
Completely independent from release_tool internals.
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from .ndjson import parse_stream, verify_prompts
from .proxy import HttpProxy
from .sandbox import SandboxConfig, TraceResult, run_sandboxed

# Root of the zenodo-publisher project (resolved from this file's location)
ZP_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Event types that can trigger auto-raise in run_test()
FAIL_EVENT_TYPES = {"fatal", "error", "warn"}
DEFAULT_FAIL_ON = {"fatal", "error"}


@dataclass
class ZpResult:
    returncode: int
    stdout: str
    stderr: str
    events: list[dict] = field(default_factory=list)
    trace: TraceResult | None = None
    http_requests: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class ZpRunner:
    """Subprocess wrapper for `uv run zp`."""

    def __init__(self, work_dir: Path, uv_binary: str = "uv",
                 sandbox: SandboxConfig | None = None,
                 use_proxy: bool = False, proxy_port: int = 8888):
        self.work_dir = work_dir
        self.uv_binary = uv_binary
        self.sandbox = sandbox
        self.use_proxy = use_proxy
        self.proxy_port = proxy_port

    def run(self, *args: str, timeout: int = 120,
            env: dict | None = None,
            log_path: Path | None = None) -> ZpResult:
        """Run zp with given arguments.

        If log_path is provided, stdout/stderr are streamed line-by-line
        to the log file as they arrive (survives crashes).
        """
        cmd = [
            self.uv_binary, "run",
            "--project", str(ZP_PROJECT_ROOT),
            "zp",
        ] + list(args)

        trace_result = None
        http_requests = []
        proxy = None

        # Start proxy if requested
        run_env = env or {}
        if self.use_proxy:
            proxy = HttpProxy(port=self.proxy_port)
            proxy_env = proxy.start()
            run_env = {**run_env, **proxy_env}

        try:
            if self.sandbox:
                result, trace_result = run_sandboxed(
                    cmd, self.sandbox,
                    cwd=self.work_dir,
                    env=run_env or None,
                    timeout=timeout,
                )
                stdout_str = result.stdout
                stderr_str = result.stderr
                returncode = result.returncode
                # Write log after the fact for sandbox mode
                if log_path:
                    self._stream_write_log(log_path, stdout_str, stderr_str)
            else:
                full_env = os.environ.copy()
                if run_env:
                    full_env.update(run_env)

                if log_path:
                    stdout_str, stderr_str, returncode = self._run_streaming(
                        cmd, cwd=self.work_dir, env=full_env,
                        timeout=timeout, log_path=log_path,
                    )
                else:
                    result = subprocess.run(
                        cmd,
                        cwd=str(self.work_dir),
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        env=full_env,
                    )
                    stdout_str = result.stdout
                    stderr_str = result.stderr
                    returncode = result.returncode
        finally:
            if proxy:
                http_requests = proxy.stop()

        events = parse_stream(stdout_str) if "--test-mode" in args else []

        return ZpResult(
            returncode=returncode,
            stdout=stdout_str,
            stderr=stderr_str,
            events=events,
            trace=trace_result,
            http_requests=http_requests,
        )

    @staticmethod
    def _run_streaming(cmd, cwd, env, timeout, log_path):
        """Run command, streaming stdout/stderr to log file line by line."""
        import threading

        log_path.parent.mkdir(exist_ok=True)
        stdout_lines = []
        stderr_lines = []

        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )

        def _drain(stream, buf, log_f, prefix):
            for line in stream:
                buf.append(line)
                log_f.write(f"[{prefix}] {line}")
                log_f.flush()

        with open(log_path, "w") as log_f:
            t_out = threading.Thread(target=_drain, args=(proc.stdout, stdout_lines, log_f, "out"))
            t_err = threading.Thread(target=_drain, args=(proc.stderr, stderr_lines, log_f, "err"))
            t_out.start()
            t_err.start()
            proc.wait(timeout=timeout)
            t_out.join()
            t_err.join()

        return "".join(stdout_lines), "".join(stderr_lines), proc.returncode

    @staticmethod
    def _stream_write_log(log_path, stdout, stderr):
        """Write stdout/stderr to log (fallback for sandbox mode)."""
        log_path.parent.mkdir(exist_ok=True)
        with open(log_path, "w") as f:
            for line in stdout.splitlines(keepends=True):
                f.write(f"[out] {line}")
            for line in stderr.splitlines(keepends=True):
                f.write(f"[err] {line}")

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

    def run_test(self, command: str,
                 config: dict | None = None,
                 test_config: dict | None = None,
                 extra_args: list[str] | None = None,
                 log_dir: Path | None = None,
                 test_name: str | None = None,
                 fail_on: set[str] | list[str] | str | None = None) -> ZpResult:
        """Run zp with inline config dicts. Writes them to tmp files, runs zp, verifies prompts, logs output.

        Args:
            command: Subcommand ("release", "archive").
            config: ZP config dict (written as zenodo_config.yaml).
            test_config: Test config dict with "prompts" and/or "cli" sections.
            extra_args: Additional CLI arguments.
            log_dir: Directory for log files.
            test_name: Test name for log file naming.
            fail_on: Event types that trigger AssertionError.
                     Default (None) = {"fatal", "error"}.
                     "ignore" = no auto-raise.
                     Set of types e.g. {"fatal", "error", "warn"}.
        """
        # Resolve fail_on
        if fail_on is None:
            fail_types = DEFAULT_FAIL_ON
        elif fail_on == "ignore":
            fail_types = set()
        elif isinstance(fail_on, (set, list)):
            fail_on = set(fail_on)
            invalid = fail_on - FAIL_EVENT_TYPES
            if invalid:
                raise ValueError(
                    f"Invalid fail_on types: {invalid}. "
                    f"Valid types: {FAIL_EVENT_TYPES}")
            fail_types = fail_on
        else:
            raise ValueError(
                f"fail_on must be a set of types, 'ignore', or None. Got: {fail_on!r}")
        args = [command]

        # Build log path
        log_path = None
        if log_dir:
            log_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = log_dir / f"{test_name or command}_{timestamp}.log"

        # Write config to tmp file and pass via --config
        tmpdir = Path(tempfile.mkdtemp())
        try:
            if config is not None:
                config_path = tmpdir / "zenodo_config.yaml"
                with open(config_path, "w") as f:
                    yaml.dump(config, f, default_flow_style=False)
                args.extend(["--config", str(config_path)])

            # Write test_config to tmp file and pass via --test-mode --test-config
            if test_config is not None:
                test_config_path = tmpdir / "test.config.yaml"
                with open(test_config_path, "w") as f:
                    yaml.dump(test_config, f, default_flow_style=False)
                args.extend(["--test-mode", "--test-config", str(test_config_path)])

                # Extract CLI args from test_config
                cli_section = test_config.get("cli", {})
                if cli_section.get("args"):
                    args.extend(cli_section["args"])

            if extra_args:
                args.extend(extra_args)

            result = self.run(*args, log_path=log_path)

            # Verify prompts if test_config defines them (unless verify_prompts=False)
            if (test_config and "prompts" in test_config
                    and test_config.get("verify_prompts", True) and result.events):
                verify_prompts(result.events, set(test_config["prompts"].keys()))

            # Auto-detect errors based on fail_on level
            if fail_types and result.events:
                matched = [e for e in result.events if e.get("type") in fail_types]
                if matched:
                    msgs = [f"[{e.get('type')}/{e.get('name', '?')}] {e.get('msg', '')}"
                            for e in matched]
                    raise AssertionError(
                        f"ZP emitted {len(matched)} event(s) matching fail_on={fail_types}:\n" +
                        "\n".join(f"  - {m}" for m in msgs)
                    )

            return result
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
