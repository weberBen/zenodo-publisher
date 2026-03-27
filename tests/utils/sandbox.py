"""Bubblewrap (bwrap) sandbox + strace tracing for E2E tests.

Provides filesystem isolation (root read-only, whitelist read-write)
and syscall tracing (files, commands, network connections).
"""

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Strace result parsing
# ---------------------------------------------------------------------------

@dataclass
class TraceResult:
    """Parsed strace output."""
    files: list[str] = field(default_factory=list)
    commands: list[list[str]] = field(default_factory=list)
    connections: list[dict] = field(default_factory=list)


def parse_strace_files(trace_path: Path) -> list[str]:
    """Extract unique file paths from openat() calls."""
    if not trace_path.exists():
        return []
    files = set()
    pattern = re.compile(r'openat\([^,]+,\s*"([^"]+)"')
    for line in trace_path.read_text(errors="replace").splitlines():
        m = pattern.search(line)
        if m:
            path = m.group(1)
            # Skip /proc, /dev, /sys noise
            if not path.startswith(("/proc/", "/dev/", "/sys/")):
                files.add(path)
    return sorted(files)


def parse_strace_commands(trace_path: Path) -> list[list[str]]:
    """Extract commands from execve() calls."""
    if not trace_path.exists():
        return []
    commands = []
    pattern = re.compile(r'execve\("([^"]+)",\s*\[([^\]]*)\]')
    for line in trace_path.read_text(errors="replace").splitlines():
        m = pattern.search(line)
        if m:
            argv_raw = m.group(2)
            argv = re.findall(r'"([^"]*)"', argv_raw)
            if argv:
                commands.append(argv)
    return commands


def parse_strace_network(trace_path: Path) -> list[dict]:
    """Extract network connections from connect() calls."""
    if not trace_path.exists():
        return []
    connections = []
    # IPv4: sa_family=AF_INET, sin_port=htons(443), sin_addr=inet_addr("1.2.3.4")
    ipv4_pattern = re.compile(
        r'connect\(\d+,\s*\{sa_family=AF_INET,\s*sin_port=htons\((\d+)\),\s*sin_addr=inet_addr\("([^"]+)"\)'
    )
    # IPv6: sa_family=AF_INET6, sin6_port=htons(443), sin6_addr=...
    ipv6_pattern = re.compile(
        r'connect\(\d+,\s*\{sa_family=AF_INET6,\s*sin6_port=htons\((\d+)\)'
    )
    for line in trace_path.read_text(errors="replace").splitlines():
        m = ipv4_pattern.search(line)
        if m:
            connections.append({"family": "inet", "port": int(m.group(1)), "addr": m.group(2)})
            continue
        m = ipv6_pattern.search(line)
        if m:
            connections.append({"family": "inet6", "port": int(m.group(1)), "addr": ""})
    return connections


def parse_strace(trace_path: Path) -> TraceResult:
    """Parse a strace output file into structured data."""
    return TraceResult(
        files=parse_strace_files(trace_path),
        commands=parse_strace_commands(trace_path),
        connections=parse_strace_network(trace_path),
    )


# ---------------------------------------------------------------------------
# Symlink validation
# ---------------------------------------------------------------------------

def check_symlinks(rw_paths: list[Path]) -> list[tuple[Path, Path]]:
    """Scan rw_paths for symlinks pointing outside allowed paths.

    Returns list of (symlink_path, target_path) for violations.
    """
    resolved_rw = [p.resolve() for p in rw_paths]
    violations = []

    for rw_path in rw_paths:
        if not rw_path.exists():
            continue
        scan = [rw_path] if rw_path.is_symlink() else []
        if rw_path.is_dir():
            scan.extend(rw_path.rglob("*"))

        for item in scan:
            if item.is_symlink():
                target = item.resolve()
                if not any(target == rw or _is_subpath(target, rw) for rw in resolved_rw):
                    violations.append((item, target))

    return violations


def _is_subpath(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Sandbox wrapper
# ---------------------------------------------------------------------------

class SandboxError(Exception):
    pass


@dataclass
class SandboxConfig:
    """Configuration for a sandboxed run."""
    rw_paths: list[Path] = field(default_factory=list)
    allow_network: bool = True
    trace: bool = True


def build_bwrap_cmd(cmd: list[str], config: SandboxConfig,
                    trace_path: Path | None = None) -> list[str]:
    """Build the full bwrap (+ optional strace) command line.

    Args:
        cmd: The command to sandbox (e.g. ["uv", "run", "zp", ...]).
        config: Sandbox configuration.
        trace_path: Path for strace output file (if tracing enabled).

    Returns:
        The wrapped command line as a list.
    """
    # Check symlinks before building
    if config.rw_paths:
        violations = check_symlinks(config.rw_paths)
        if violations:
            details = "\n".join(f"  {link} -> {target}" for link, target in violations)
            raise SandboxError(
                f"Symlinks pointing outside sandbox scope:\n{details}\n"
                f"Declare the target paths in rw_paths to allow access."
            )

    bwrap = ["bwrap"]

    # Root filesystem read-only
    bwrap.extend(["--ro-bind", "/", "/"])

    # Mount rw paths
    for rw in config.rw_paths:
        path_str = str(rw.resolve())
        bwrap.extend(["--bind", path_str, path_str])

    # /dev, /proc, /tmp
    bwrap.extend(["--dev", "/dev"])
    bwrap.extend(["--proc", "/proc"])
    bwrap.extend(["--tmpfs", "/tmp"])

    # Network isolation
    if not config.allow_network:
        bwrap.append("--unshare-net")

    # Add the actual command
    bwrap.extend(["--"])
    bwrap.extend(cmd)

    # Wrap with strace if tracing
    if config.trace and trace_path:
        strace = [
            "strace", "-f",
            "-e", "trace=openat,execve,connect,sendto,recvfrom",
            "-o", str(trace_path),
        ]
        return strace + bwrap

    return bwrap


def run_sandboxed(cmd: list[str], config: SandboxConfig,
                  cwd: str | Path | None = None,
                  env: dict | None = None,
                  timeout: int = 120) -> tuple[subprocess.CompletedProcess, TraceResult | None]:
    """Run a command inside a bwrap sandbox with optional strace tracing.

    Returns:
        Tuple of (CompletedProcess, TraceResult or None).
    """
    trace_path = None
    trace_result = None

    if config.trace:
        trace_fd, trace_file = tempfile.mkstemp(suffix=".strace")
        os.close(trace_fd)
        trace_path = Path(trace_file)

    full_cmd = build_bwrap_cmd(cmd, config, trace_path)

    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    result = subprocess.run(
        full_cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=run_env,
    )

    if trace_path and trace_path.exists():
        trace_result = parse_strace(trace_path)
        trace_path.unlink(missing_ok=True)

    return result, trace_result
