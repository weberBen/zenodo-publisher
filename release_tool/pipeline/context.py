"""Pipeline context, hook points, and registry for the release pipeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..archive_operation import FileEntry
    from ..config.release import ReleaseConfig


# ---------------------------------------------------------------------------
# Pipeline state
# ---------------------------------------------------------------------------

@dataclass
class PipelineContext:
    """Shared mutable state threaded through every pipeline handler."""
    config: "ReleaseConfig"
    output_dir: Path
    tag_name: str = ""
    commit_env: dict = field(default_factory=dict)
    archived_files: list["FileEntry"] = field(default_factory=list)
    record_info: dict | None = None


# ---------------------------------------------------------------------------
# Hook points — ordered sequence of pipeline phases
# ---------------------------------------------------------------------------

class HookPoint(str, Enum):
    """Named phases of the release pipeline, executed in declaration order."""
    MODULE_CHECK    = "module_check"      # verify modules exist + pass self-check
    GIT_CHECK       = "git_check"
    RELEASE         = "release"
    COMMIT_INFO     = "commit_info"
    PROJECT_NAME    = "project_name"
    COMPILE         = "compile"
    POST_COMPILE    = "post_compile"      # re-check git + verify release still valid
    RESOLVE_FILES   = "resolve_files"
    ARCHIVE         = "archive"
    HASH            = "hash"
    MANIFEST        = "manifest"
    SIGN            = "sign"
    IDENTIFIERS     = "identifiers"
    CUSTOM_MODULES  = "custom_modules"    # subprocess custom modules
    PUBLISH         = "publish"
    PERSIST         = "persist"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

Handler = Callable[[PipelineContext], None]


class HookRegistry:
    """Maps HookPoint → ordered list of handlers; fires them in sequence."""

    def __init__(self) -> None:
        self._handlers: dict[HookPoint, list[Handler]] = {hp: [] for hp in HookPoint}

    def register(self, hook_point: HookPoint, handler: Handler) -> None:
        """Append handler to hook_point's handler list."""
        self._handlers[hook_point].append(handler)

    def fire(self, hook_point: HookPoint, ctx: PipelineContext) -> None:
        """Call all handlers registered at hook_point with ctx."""
        for handler in self._handlers[hook_point]:
            handler(ctx)

    def run_pipeline(self, ctx: PipelineContext) -> None:
        """Execute all hook points in declaration order."""
        for hp in HookPoint:
            self.fire(hp, ctx)
