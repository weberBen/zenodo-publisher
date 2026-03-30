"""Standardized output for the release tool.

All console output goes through these helpers. Internally, every call produces
a JSON event dict.  In normal mode the event is formatted for humans; in test
mode each event is printed as a single NDJSON line on stdout.

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
import traceback
from dataclasses import dataclass
from pathlib import Path
from .errors import normalize_name

if os.name == "posix":  # Linux, macOS, BSD...
    import termios
else:  # Windows
    import msvcrt

RED_UNDERLINE = "\033[91;4m"
RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Stack trace formatting
# ---------------------------------------------------------------------------

def _extract_frames(exc: BaseException) -> list[str]:
    """Extract release_tool frames from an exception's traceback."""
    tb = traceback.extract_tb(exc.__traceback__)
    parts = []
    for frame in tb:
        if "release_tool" not in frame.filename:
            continue
        module = Path(frame.filename).stem
        parts.append(f"{module}:{frame.name}")
    return parts


def format_trace(exc: BaseException) -> str:
    """Format exception traceback as file(func).file(func)... pipe.

    Only includes frames from the release_tool package.
    Walks the exception chain (__cause__) for chained exceptions.
    """
    parts = _extract_frames(exc)
    cause = exc.__cause__
    while cause:
        parts.extend(_extract_frames(cause))
        cause = cause.__cause__
    return ".".join(parts)

def _enrich_event_from_exc(event: dict, exc: Exception) -> None:
    """Add exc, error_type, name (from ZPError), and pipe to an event dict."""
    from .errors import ZPError

    event["exc"] = str(exc)
    event["error_type"] = type(exc).__name__
    if isinstance(exc, ZPError) and exc.name:
        existing = event.get("name")
        event["name"] = normalize_name(existing, suffix=exc.name)
    pipe = format_trace(exc)
    if pipe:
        event["pipe"] = pipe


# ---------------------------------------------------------------------------
# Output class
# ---------------------------------------------------------------------------

class Output:
    """Unified output handler: state + emit + formatting + public API."""

    def __init__(self):
        self.label = ""
        self.test_mode = False
        self.test_config = None  # TestConfig | None
        self._debug = False

    # -- Setup --------------------------------------------------------------

    def setup(self, project_name: str = "", debug: bool = False,
              test_mode: bool = False, test_config=None):
        """Configure output.  Called once at pipeline start."""
        self.test_mode = test_mode
        self.test_config = test_config
        self._debug = debug
        self.label = f"({RED_UNDERLINE}{project_name}{RESET})" if project_name else ""

    def before_init_setup(self, debug: bool = False, test_mode: bool = False):
        self.setup("ZP", debug=debug, test_mode=test_mode)
    # -- Test config --------------------------------------------------------

    def get_test_response(self, name: str):
        """Get test response for a prompt by name. Raises if not found."""
        if self.test_config is None:
            raise RuntimeError("No test config loaded but test mode is active")
        prompts = self.test_config.prompts
        if name not in prompts:
            raise RuntimeError(
                f"No test response for prompt '{name}' in test config. "
                f"Available: {list(prompts.keys())}"
            )
        return prompts[name]

    # -- Core build / emit / format -----------------------------------------

    @staticmethod
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

    def emit(self, event: dict):
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

        if self.test_mode:
            print(json.dumps(event, default=str), flush=True)
        else:
            self._format_human(event)

    def _format_human(self, event: dict):
        """Translate a JSON event into the legacy human-friendly output."""
        t = event["type"]
        msg = event.get("msg", "")
        data = event.get("data", {})

        # Resolve template if data present
        if data and "{" in msg:
            msg = msg.format(**data)

        if t == "step":
            print(f"{self.label} {msg}")
        elif t == "step_ok":
            if not event.get("silent"):
                print(f"{self.label} \u2705 {msg}\n")
            else:
                print(msg)
        elif t == "step_warn":
            print(f"\n{self.label} \u26a0\ufe0f {msg}\n")
        elif t == "info":
            print(msg)
        elif t == "info_ok":
            print(f"\u2713 {msg}")
        elif t == "detail":
            print(f"  {msg}")
        elif t == "detail_ok":
            print(f"  \u2713 {msg}")
        elif t == "detail_skip":
            print(f"  \u2014 {msg}")
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
            if self._debug:
                print(f"  $ {msg}")
        elif t == "debug":
            if self._debug:
                print(f"  [debug] {msg}")
        elif t == "data":
            # Structured data: no human output (visible in --debug)
            if self._debug:
                print(f"  [data:{event.get('code', '?')}] {json.dumps(event.get('value', ''), default=str)}")
        elif t in ("prompt", "confirm"):
            pass  # handled by Prompt.ask(), not _format_human
        # unknown types are silently ignored

    # -- Public API: step level (pipeline, with project label) --------------

    def step(self, msg: str, **kwargs):
        self.emit(self._build_event("step", msg, **kwargs))

    def step_ok(self, msg: str, **kwargs):
        self.emit(self._build_event("step_ok", msg, **kwargs))

    def step_warn(self, msg: str, **kwargs):
        self.emit(self._build_event("step_warn", msg, **kwargs))

    # -- Public API: info level (no label) ----------------------------------

    def info(self, msg: str, **kwargs):
        self.emit(self._build_event("info", msg, **kwargs))

    def info_ok(self, msg: str, **kwargs):
        self.emit(self._build_event("info_ok", msg, **kwargs))

    # -- Public API: detail level (indented) --------------------------------

    def detail(self, msg: str, **kwargs):
        self.emit(self._build_event("detail", msg, **kwargs))

    def detail_ok(self, msg: str, **kwargs):
        self.emit(self._build_event("detail_ok", msg, **kwargs))

    def detail_skip(self, msg: str, **kwargs):
        self.emit(self._build_event("detail_skip", msg, **kwargs))

    # -- Public API: warn / error / debug -----------------------------------

    def warn(self, msg: str, **kwargs):
        self.emit(self._build_event("warn", msg, **kwargs))

    def error(self, msg: str, exc: Exception | None = None, **kwargs):
        event = self._build_event("error", msg, **kwargs)
        if exc:
            _enrich_event_from_exc(event, exc)
        self.emit(event)

    def fatal(self, msg: str, exc: Exception | None = None, **kwargs):
        if exc and str(exc) != msg:
            msg = f"{msg}\n{str(exc)}"
        event = self._build_event("fatal", msg, **kwargs)
        if exc:
            _enrich_event_from_exc(event, exc)
        self.emit(event)

    def debug(self, msg: str, **kwargs):
        self.emit(self._build_event("debug", msg, **kwargs))

    def cmd(self, args: list[str]):
        self.emit({"type": "cmd", "msg": " ".join(args)})

    # -- Public API: structured data (captured by tests) --------------------

    def data(self, code: str, value):
        """Emit a structured data event.

        In test mode this is a regular NDJSON line; in human mode it is only
        visible with --debug.
        """
        self.emit({"type": "data", "code": code, "value": value})

    # -- User input helpers -------------------------------------------------

    @staticmethod
    def flush_stdin():
        if not sys.stdin.isatty():
            return
        if os.name == "posix":
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
        else:
            while msvcrt.kbhit():
                msvcrt.getch()


# ---------------------------------------------------------------------------
# Module-level instance + aliases
# ---------------------------------------------------------------------------

_out = Output()

before_init_setup = _out.before_init_setup
setup = _out.setup
step = _out.step
step_ok = _out.step_ok
step_warn = _out.step_warn
info = _out.info
info_ok = _out.info_ok
detail = _out.detail
detail_ok = _out.detail_ok
detail_skip = _out.detail_skip
warn = _out.warn
error = _out.error
fatal = _out.fatal
debug = _out.debug
cmd = _out.cmd
data = _out.data
emit = _out.emit


# ---------------------------------------------------------------------------
# Prompt system
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PromptOption:
    """A single option for a prompt."""
    name: str         # returned value: "yes", "no", "yall", "nall", "text", "text_optional"
    complete: str     # long form: "yes", "no", "yes all", "no all"
    light: str        # short form: "y", "n", "yall", "nall"
    is_accept: bool   # True for affirmative options


YES     = PromptOption("yes",  "yes",     "y",    True)
NO      = PromptOption("no",   "no",      "n",    False)
YES_ALL = PromptOption("yall", "yes all", "yall", True)
NO_ALL  = PromptOption("nall", "no all",  "nall", False)
ENTER   = PromptOption("enter", "",  "", True)

# Text options: special markers detected by Prompt to switch to text mode
TEXT          = PromptOption("text",          "", "", True)
TEXT_OPTIONAL = PromptOption("text_optional", "", "", True)


@dataclass(frozen=True)
class PromptResult:
    """Result returned by Prompt.ask()."""
    name: str        # prompt name (e.g. "confirm_build", "enter_tag")
    is_accept: bool  # True if the answer is affirmative / valid
    value: str       # option name for confirm ("yes", "no", ...) or text for text prompts


class Prompt:
    """Unified prompt class for both confirmations and text input.

    Text mode is activated when options contain TEXT or TEXT_OPTIONAL.
    In text mode, ask() reads free-text input and validates non-empty (TEXT)
    or accepts empty (TEXT_OPTIONAL).

    Confirm mode validates input against the configured options.

    Args:
        options: List of PromptOption instances.
        name: Unique prompt identifier (mandatory). Used for registry and test config.
        level: Accepted form level -- "danger", "light", or "complete" (confirm mode only).
        enter_confirms: If True, empty input returns the first accept option (confirm mode).
        double_confirm: If True, after enter/shortcut confirm, ask "are you sure?" (confirm mode).
        secure_value: If set, typing this exact string counts as accept (confirm mode).
    """

    _registry: dict[str, str] = {}  # name -> "text" | "confirm"

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
        self.options = list(options)

        # Detect text mode
        self._is_text = any(o.name in ("text", "text_optional") for o in self.options)
        kind = "text" if self._is_text else "confirm"
        Prompt._register(name, kind)

        if not self._is_text:
            self.level = level
            self.enter_confirms = enter_confirms or level == "danger"
            if self.enter_confirms:
                self.options.append(ENTER)
            self.double_confirm = double_confirm
            self.secure_value = secure_value
            if self.secure_value:
                self.options = [PromptOption("secure_value", secure_value, secure_value, True)]
            self._accepted = self._build_accepted()

    @classmethod
    def _register(cls, name: str, kind: str):
        if not name:
            raise RuntimeError("Prompt must have a name")
        if name in cls._registry:
            if cls._registry[name] == kind:
                return  # same name+kind = OK (loop re-entry on same prompt)
            raise RuntimeError(f"Duplicate prompt name '{name}' with different kind")
        cls._registry[name] = kind

    @classmethod
    def get_registry(cls) -> dict[str, str]:
        """Return a copy of the prompt registry (name -> kind)."""
        return dict(cls._registry)

    # -- Confirm mode internals --

    def _build_accepted(self) -> dict[str, PromptOption]:
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
        return [o.name for o in self.options]

    @property
    def hint(self) -> str:
        if self._is_text:
            return ""
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

    # -- Main API --

    def ask(self, message: str) -> PromptResult:
        """Prompt the user and return a PromptResult.

        In test mode, looks up the response from the test config.
        """
        if self._is_text:
            return self._ask_text(message)
        return self._ask_confirm(message)

    # -- Text mode --

    def _ask_text(self, message: str) -> PromptResult:
        optional = any(o.name == "text_optional" for o in self.options)

        if _out.test_mode:
            value = str(_out.get_test_response(self.name))
            _out.emit({"type": "prompt", "name": self.name, "msg": message, "response": value})
            is_accept = bool(value) or optional
            return PromptResult(name=self.name, is_accept=is_accept, value=value)

        _out.flush_stdin()
        value = input(f"{message}: ").strip()
        is_accept = bool(value) or optional
        return PromptResult(name=self.name, is_accept=is_accept, value=value)

    # -- Confirm mode --

    def _ask_confirm(self, message: str) -> PromptResult:
        if _out.test_mode:
            response = str(_out.get_test_response(self.name))
            match = next((o for o in self.options if o.name == response), None)
            if match is None:
                raise RuntimeError(
                    f"Invalid test response '{response}' for prompt '{self.name}'. "
                    f"Valid options: {self.option_names}"
                )
            _out.emit({"type": "confirm", "name": self.name, "msg": message,
                       "options": self.option_names, "response": response})
            return PromptResult(name=self.name, is_accept=match.is_accept, value=match.name)

        match = self._ask_input(f"{message} [{self.hint}]", self._accepted)

        if self.double_confirm:
            match = self._ask_input("Are you sure? [y/n/Enter]",
                                    {o.light.lower(): o for o in [YES, NO, ENTER]}
                                    | {o.complete.lower(): o for o in [YES, NO, ENTER]})

        return PromptResult(name=self.name, is_accept=match.is_accept, value=match.name)

    def _ask_input(self, message: str, accepted: dict) -> PromptOption:
        match = None
        while match is None:
            _out.flush_stdin()
            response = input(f"{message}: ").strip()

            if not response:
                response = ""

            match = accepted.get(response.lower())
            if not match and response:
                return NO

        return match
