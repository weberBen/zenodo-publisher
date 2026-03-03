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
from dataclasses import dataclass
import termios

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
    logger.info(f"{_label} {msg}")


def step_ok(msg: str, silent=False):
    """Step success: '(project) ✅ Done!'"""
    if not silent:
        logger.info(f"{_label} ✅ {msg}\n")
    else:
        logger.info(f"{msg}")


def step_warn(msg: str):
    """Step warning: '\\n(project) ⚠️ Skipping...'"""
    logger.warning(f"\n{_label} ⚠️ {msg}\n")


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


# --- User input ---

def _flush_stdin():
    """Discard any pending input in stdin (e.g. extra Enter presses)."""
    try:
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except (termios.error, ValueError):
        pass


def prompt(msg: str) -> str:
    """Prompt the user for input, flushing any buffered keystrokes first."""
    _flush_stdin()
    return input(f"{msg}: ").strip()


# --- Confirmation prompt ---

@dataclass(frozen=True)
class PromptOption:
    """A single option for a confirmation prompt."""
    name: str         # returned value: "yes", "no", "yall", "nall"
    complete: str     # long form: "yes", "no", "yes all", "no all"
    light: str        # short form: "y", "n", "yall", "nall"
    is_accept: bool   # True for affirmative options


YES     = PromptOption("yes",  "yes",     "y",    True)
NO      = PromptOption("no",   "no",      "n",    False)
YES_ALL = PromptOption("yall", "yes all", "yall", True)
NO_ALL  = PromptOption("nall", "no all",  "nall", False)
ENTER   = PromptOption("enter", "",  "", True)


class ConfirmPrompt:
    """Reusable confirmation prompt with configurable validation level.

    Args:
        options: List of PromptOption instances available for this prompt.
        level: Accepted form level — "danger", "light", or "complete".
            - danger: enter_confirms implied, any input accepted.
            - light: both light and complete forms accepted.
            - complete: only complete forms accepted.
        enter_confirms: If True, empty input returns the first accept option.
        double_confirm: If True, after enter/shortcut confirm, ask "are you sure?".
        secure_value: If set, typing this exact string counts as accept.
    """

    def __init__(
        self,
        options: list[PromptOption],
        level: str = "light",
        enter_confirms: bool = False,
        double_confirm: bool = False,
        secure_value: str | None = None,
    ):
        self.options = options
        self.level = level
        
        self.enter_confirms = enter_confirms or level == "danger"
        if self.enter_confirms:
            self.options.append(ENTER)
        
        self.double_confirm = double_confirm
        
        self.secure_value = secure_value
        if self.secure_value:
            self.options = [PromptOption("secure_value", secure_value,  secure_value, True)]
        
        self._accepted = self._build_accepted()

    def _build_accepted(self) -> dict[str, PromptOption]:
        """Build mapping of accepted input strings to their PromptOption."""
        accepted = {}
        for opt in self.options:
            if self.level in ["danger", "light"]:
                # Any input accepted, map both forms
                accepted[opt.light.lower()] = opt
                accepted[opt.complete.lower()] = opt
            else:
                accepted[opt.complete.lower()] = opt
        
        return accepted

    @property
    def hint(self) -> str:
        """Auto-generated hint string from active forms."""
        if self.secure_value:
            return self.secure_value
        if self.enter_confirms and not self.double_confirm:
            return "Enter"
        parts = []
        for opt in self.options:
            if self.level == "complete":
                parts.append(opt.complete)
            else:
                parts.append(opt.light)
        return "/".join(parts)

    def _ask(self, message: str, accepted: dict) -> PromptOption:
        match = None
        while match is None:
            response = prompt(message)
            
            # Empty input
            if not response:
                response = ""
            
            # Match against accepted forms
            match = accepted.get(response.lower())
            if not match and response:
                return NO
        
        return match
    
    def ask(self, message: str) -> PromptOption:
        """Prompt the user until a valid option is entered.

        Returns:
            The matched PromptOption.
        """
        
        match = self._ask(f"{message} [{self.hint}]", self._accepted)
        
        if self.double_confirm:
            match = self.ask("Are you sure? [y/n/Enter]", {YES, NO, ENTER})
        
        return match
