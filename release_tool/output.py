"""Standardized logging output for the release tool.

All console output should go through these helpers to ensure consistent
formatting across the pipeline and sub-modules.

Levels:
    step / step_ok / step_warn  — pipeline step headers (with project label)
    info / info_ok              — top-level messages in sub-modules (no label)
    detail / detail_ok          — indented sub-operation messages
    warn / error                — warnings and errors
    debug                       — only shown when debug=True
"""

import logging
import sys

RED_UNDERLINE = "\033[91;4m"
RESET = "\033[0m"

_label = ""
logger = logging.getLogger("release_tool")


def setup(project_name: str = "", debug: bool = False):
    """Configure the logger. Called once at pipeline start."""
    global _label
    _label = f"({RED_UNDERLINE}{project_name}{RESET})" if project_name else ""

    level = logging.DEBUG if debug else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)

    # python-gnupg uses a logger named "gnupg" internally to log gpg command
    # lines and status messages (see https://gnupg.readthedocs.io/en/stable/).
    # By attaching our handler, these messages appear in --debug output without
    # any explicit logging calls in gpg_operations.py.
    gnupg_logger = logging.getLogger("gnupg")
    gnupg_logger.addHandler(handler)
    gnupg_logger.setLevel(level)


# --- Step level (pipeline, with project label) ---

def step(msg: str):
    """Step header: '\\n(project) 🔍 Checking git...'"""
    logger.info(f"\n{_label} {msg}")


def step_ok(msg: str):
    """Step success: '(project) ✅ Done!'"""
    logger.info(f"{_label} ✅ {msg}")


def step_warn(msg: str):
    """Step warning: '\\n(project) ⚠️ Skipping...'"""
    logger.warning(f"\n{_label} ⚠️ {msg}")


# --- Info level (no label, top-level messages in sub-modules) ---

def info(msg: str):
    """Plain info message."""
    logger.info(msg)


def info_ok(msg: str):
    """Info success: '✓ Repository up to date'"""
    logger.info(f"✓ {msg}")


# --- Detail level (indented, sub-operations) ---

def detail(msg: str):
    """Sub-operation: '  Uploading file.pdf...'"""
    logger.info(f"  {msg}")


def detail_ok(msg: str):
    """Sub-operation success: '  ✓ file.pdf uploaded'"""
    logger.info(f"  ✓ {msg}")


# --- Warning / Error / Debug ---

def warn(msg: str):
    """Warning without label: '⚠️ Something wrong'"""
    logger.warning(f"⚠️ {msg}")


def error(msg: str, exc: Exception | None = None):
    """Error: '❌ Something failed'"""
    logger.error(f"❌ {msg}")
    if exc:
        logger.error(str(exc))


def fatal(msg: str):
    """Fatal error with decorative framing."""
    logger.error(f"\n💀❌💀 {RED_UNDERLINE}{msg}{RESET} 💀❌💀")


def debug(msg: str):
    """Debug (hidden when debug=False): '  [debug] value=42'"""
    logger.debug(f"  [debug] {msg}")


def cmd(args: list[str]):
    """Log a subprocess command (debug only): '  $ git status'"""
    logger.debug(f"  $ {' '.join(args)}")
