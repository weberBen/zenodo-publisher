"""HTTP proxy wrapper using mitmproxy (mitmdump) for E2E tests.

Captures all HTTP/HTTPS requests made by ZP during a test run.
"""

import json
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HttpRequest:
    method: str
    url: str
    status: int
    content_type: str = ""


class HttpProxy:
    """mitmdump wrapper that captures HTTP traffic."""

    def __init__(self, port: int = 8888, log_dir: Path | None = None):
        self.port = port
        self.log_dir = log_dir
        self._process: subprocess.Popen | None = None
        self._flow_file: Path | None = None
        self._script_file: Path | None = None

    def start(self) -> dict[str, str]:
        """Start mitmdump and return env vars to pass to the subprocess.

        Returns:
            Dict with HTTP_PROXY, HTTPS_PROXY, REQUESTS_CA_BUNDLE keys.
        """
        # Create a temp file for captured requests (NDJSON)
        fd, flow_path = tempfile.mkstemp(suffix=".ndjson")
        import os
        os.close(fd)
        self._flow_file = Path(flow_path)

        # Write inline mitmproxy script that logs requests as NDJSON
        fd, script_path = tempfile.mkstemp(suffix=".py")
        os.close(fd)
        self._script_file = Path(script_path)
        self._script_file.write_text(f"""
import json

FLOW_FILE = "{flow_path}"

def response(flow):
    entry = {{
        "method": flow.request.method,
        "url": flow.request.pretty_url,
        "status": flow.response.status_code,
        "content_type": flow.response.headers.get("content-type", ""),
    }}
    with open(FLOW_FILE, "a") as f:
        f.write(json.dumps(entry) + "\\n")
""")

        # Find mitmproxy CA cert
        ca_cert = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"

        # Start mitmdump
        cmd = [
            "mitmdump",
            "--listen-port", str(self.port),
            "--set", "ssl_insecure=true",
            "-s", str(self._script_file),
            "-q",  # quiet
        ]
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for proxy to be ready
        time.sleep(1)

        env = {
            "HTTP_PROXY": f"http://localhost:{self.port}",
            "HTTPS_PROXY": f"http://localhost:{self.port}",
        }
        if ca_cert.exists():
            env["REQUESTS_CA_BUNDLE"] = str(ca_cert)

        return env

    def stop(self) -> list[dict]:
        """Stop mitmdump and return captured requests."""
        requests = []

        if self._process:
            self._process.send_signal(signal.SIGINT)
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None

        if self._flow_file and self._flow_file.exists():
            for line in self._flow_file.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        requests.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
            self._flow_file.unlink(missing_ok=True)

        if self._script_file and self._script_file.exists():
            self._script_file.unlink(missing_ok=True)

        return requests
