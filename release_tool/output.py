"""Standardized output for the release tool.

All console output goes through these helpers. Internally, every call produces
a JSON event dict.  In normal mode the event is formatted for humans; in test
mode (``--test-mode``) each event is printed as a single NDJSON line on stdout.

Levels:
    step / step_ok / step_warn  -- pipeline step headers (with project label)
    info / info_ok              -- top-level messages in sub-modules (no label)
    detail / detail_ok          -- indented sub-operation messages
    warn / error                -- warnings and errors
    debug                       -- only shown when debug=True
    data                        -- structured data for test capture (no human output)
"""

import json
import sys
import os

if os.name == "posix":  # Linux, macOS, BSD...
    import termios
else:  # Windows
    import msvcrt

RED_UNDERLINE = "\033[91;4m"
RESET = "\033[0m"

_label = ""
_test_mode = False
_debug = False


# ---------------------------------------------------------------------------
# Prompt registry
# ---------------------------------------------------------------------------

_prompt_registry: dict[str, str] = {}  # name -> "prompt" | "confirm"


def _register_prompt(name: str, kind: str):
    if not name:
        raise RuntimeError("Prompt must have a name")
    if name in _prompt_registry:
        if _prompt_registry[name] == kind:
            return  # same name+kind = OK (loop re-entry on same prompt)
        raise RuntimeError(f"Duplicate prompt name '{name}' with different kind")
    _prompt_registry[name] = kind


def get_prompt_registry() -> dict[str, str]:
    """Return a copy of the prompt registry (name -> kind)."""
    return dict(_prompt_registry)


# ---------------------------------------------------------------------------
# Test config
# ---------------------------------------------------------------------------

_test_config: dict | None = None


def load_test_config(path: str):
    """Load test config YAML. Called by CLI when --test-config is provided."""
    global _test_config
    import yaml
    with open(path) as f:
        _test_config = yaml.safe_load(f) or {}


def _get_test_response(name: str):
    """Get test response for a prompt by name. Raises if not found."""
    if _test_config is None:
        raise RuntimeError("No test config loaded but test mode is active")
    prompts = _test_config.get("prompts", {})
    if name not in prompts:
        raise RuntimeError(
            f"No test response for prompt '{name}' in test config. "
            f"Available: {list(prompts.keys())}"
        )
    return prompts[name]


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup(project_name: str = "", debug: bool = False, test_mode: bool = False):
    """Configure output.  Called once at pipeline start."""
    global _label, _test_mode, _debug
    _test_mode = test_mode
    _debug = debug
    _label = f"({RED_UNDERLINE}{project_name}{RESET})" if project_name else ""


# ---------------------------------------------------------------------------
# Core build / emit / format
# ---------------------------------------------------------------------------

def _build_event(type: str, msg: str, **kwargs) -> dict:
    """Build a JSON event from type, msg template, and kwargs.

    Reserved kwargs (become top-level event fields):
        name, code, silent

    All remaining kwargs go into event["data"].
    """
    event = {"type": type, "msg": msg}
    for key in ("name", "code", "silent"):
        if key in kwargs:
            event[key] = kwargs.pop(key)
    if kwargs:
        event["data"] = kwargs
    return event


def _emit(event: dict):
    """Single entry point: NDJSON in test mode, human formatting otherwise."""
    data = event.get("data", {})
    msg = event.get("msg", "")

    # Validate: every {key} in msg must exist in data
    if data and "{" in msg:
        try:
            msg.format(**data)
        except KeyError as e:
            raise RuntimeError(
                f"output template references unknown key {e}: "
                f"msg={msg!r}, data keys={list(data.keys())}"
            )

    if _test_mode:
        print(json.dumps(event, default=str), flush=True)
    else:
        _format_human(event)


def _format_human(event: dict):
    """Translate a JSON event into the legacy human-friendly output."""
    t = event["type"]
    msg = event.get("msg", "")
    data = event.get("data", {})

    # Resolve template if data present
    if data and "{" in msg:
        msg = msg.format(**data)

    if t == "step":
        print(f"{_label} {msg}")
    elif t == "step_ok":
        if not event.get("silent"):
            print(f"{_label} \u2705 {msg}\n")
        else:
            print(msg)
    elif t == "step_warn":
        print(f"\n{_label} \u26a0\ufe0f {msg}\n")
    elif t == "info":
        print(msg)
    elif t == "info_ok":
        print(f"\u2713 {msg}")
    elif t == "detail":
        print(f"  {msg}")
    elif t == "detail_ok":
        print(f"  \u2713 {msg}")
    elif t == "warn":
        print(f"\u26a0\ufe0f {msg}")
    elif t == "error":
        txt = f"\u274c {msg}"
        exc = event.get("exc")
        if exc:
            txt += f"\n{exc}"
        print(txt)
    elif t == "fatal":
        print(f"\n\U0001f480\u274c\U0001f480 {RED_UNDERLINE}{msg}{RESET} \U0001f480\u274c\U0001f480")
    elif t == "cmd":
        if _debug:
            print(f"  $ {msg}")
    elif t == "debug":
        if _debug:
            print(f"  [debug] {msg}")
    elif t == "data":
        # Structured data: no human output (visible in --debug)
        if _debug:
            print(f"  [data:{event.get('code', '?')}] {json.dumps(event.get('value', ''), default=str)}")
    elif t in ("prompt", "confirm"):
        pass  # handled by prompt()/ConfirmPrompt.ask(), not _format_human
    # unknown types are silently ignored


# ---------------------------------------------------------------------------
# Public API -- step level (pipeline, with project label)
# ---------------------------------------------------------------------------

def step(msg: str, **kwargs):
    _emit(_build_event("step", msg, **kwargs))


def step_ok(msg: str, **kwargs):
    _emit(_build_event("step_ok", msg, **kwargs))


def step_warn(msg: str, **kwargs):
    _emit(_build_event("step_warn", msg, **kwargs))


# ---------------------------------------------------------------------------
# Public API -- info level (no label)
# ---------------------------------------------------------------------------

def info(msg: str, **kwargs):
    _emit(_build_event("info", msg, **kwargs))


def info_ok(msg: str, **kwargs):
    _emit(_build_event("info_ok", msg, **kwargs))


# ---------------------------------------------------------------------------
# Public API -- detail level (indented)
# ---------------------------------------------------------------------------

def detail(msg: str, **kwargs):
    _emit(_build_event("detail", msg, **kwargs))


def detail_ok(msg: str, **kwargs):
    _emit(_build_event("detail_ok", msg, **kwargs))


# ---------------------------------------------------------------------------
# Public API -- warn / error / debug
# ---------------------------------------------------------------------------

def warn(msg: str, **kwargs):
    _emit(_build_event("warn", msg, **kwargs))


def error(msg: str, exc: Exception | None = None, **kwargs):
    event = _build_event("error", msg, **kwargs)
    if exc:
        event["exc"] = str(exc)
        event["error_type"] = type(exc).__name__
    _emit(event)


def fatal(msg: str, **kwargs):
    _emit(_build_event("fatal", msg, **kwargs))


def debug(msg: str, **kwargs):
    _emit(_build_event("debug", msg, **kwargs))


def cmd(args: list[str]):
    _emit({"type": "cmd", "msg": " ".join(args)})


# ---------------------------------------------------------------------------
# Public API -- structured data (captured by tests)
# ---------------------------------------------------------------------------

def data(code: str, value):
    """Emit a structured data event.

    In test mode this is a regular NDJSON line; in human mode it is only
    visible with --debug.
    """
    _emit({"type": "data", "code": code, "value": value})


# ---------------------------------------------------------------------------
# User input
# ---------------------------------------------------------------------------

def _flush_stdin():
    if not sys.stdin.isatty():
        return
    if os.name == "posix":
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    else:
        while msvcrt.kbhit():
            msvcrt.getch()


def prompt(msg: str, *, name: str) -> str:
    """Prompt the user for free-text input.

    Args:
        msg: The prompt message displayed to the user.
        name: Unique prompt identifier (mandatory). Used for test config lookup.
    """
    _register_prompt(name, "prompt")
    if _test_mode:
        value = str(_get_test_response(name))
        _emit({"type": "prompt", "name": name, "msg": msg, "response": value})
        return value
    _flush_stdin()
    return input(f"{msg}: ").strip()


# ---------------------------------------------------------------------------
# Confirmation prompt
# ---------------------------------------------------------------------------

from dataclasses import dataclass


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
        name: Unique prompt identifier (mandatory). Used for registry and test config.
        level: Accepted form level -- "danger", "light", or "complete".
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
        *,
        name: str,
        level: str = "light",
        enter_confirms: bool = False,
        double_confirm: bool = False,
        secure_value: str | None = None,
    ):
        self.name = name
        self.options = list(options)  # copy to avoid mutating caller's list
        self.level = level

        _register_prompt(name, "confirm")

        self.enter_confirms = enter_confirms or level == "danger"
        if self.enter_confirms:
            self.options.append(ENTER)

        self.double_confirm = double_confirm

        self.secure_value = secure_value
        if self.secure_value:
            self.options = [PromptOption("secure_value", secure_value, secure_value, True)]

        self._accepted = self._build_accepted()

    def _build_accepted(self) -> dict[str, PromptOption]:
        """Build mapping of accepted input strings to their PromptOption."""
        accepted = {}
        for opt in self.options:
            if self.level in ["danger", "light"]:
                accepted[opt.light.lower()] = opt
                accepted[opt.complete.lower()] = opt
            else:
                accepted[opt.complete.lower()] = opt

        return accepted

    @property
    def option_names(self) -> list[str]:
        """List of valid option names for this prompt."""
        return [o.name for o in self.options]

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
            _flush_stdin()
            response = input(f"{message}: ").strip()

            if not response:
                response = ""

            match = accepted.get(response.lower())
            if not match and response:
                return NO

        return match

    def ask(self, message: str) -> PromptOption:
        """Prompt the user until a valid option is entered.

        In test mode, looks up the response from the test config file.
        """
        if _test_mode:
            response = str(_get_test_response(self.name))
            match = next((o for o in self.options if o.name == response), None)
            if match is None:
                raise RuntimeError(
                    f"Invalid test response '{response}' for prompt '{self.name}'. "
                    f"Valid options: {self.option_names}"
                )
            _emit({"type": "confirm", "name": self.name, "msg": message,
                   "options": self.option_names, "response": response})
            return match

        match = self._ask(f"{message} [{self.hint}]", self._accepted)

        if self.double_confirm:
            match = self._ask("Are you sure? [y/n/Enter]",
                              {o.light.lower(): o for o in [YES, NO, ENTER]}
                              | {o.complete.lower(): o for o in [YES, NO, ENTER]})

        return match
