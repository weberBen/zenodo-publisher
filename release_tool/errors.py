"""Base exception classes for zenodo-publisher."""


import re

class ZPError(Exception):
    """Base exception with optional scoped name for structured output."""

    _prefix: str | None = None

    def __init__(self, message: str, *, name: str | None = None, exc: Exception = None):
        super().__init__(message)
        suffix = getattr(exc, "name", None) if exc else None
        self.name = normalize_name(name, prefix=self._prefix, suffix=suffix)

def collapse_name(name):
    items = name.strip().split('.')
    prev_item = None
    new_items = []
    
    for item in items:
        if item == prev_item:
            continue
        
        prev_item = item
        new_items.append(item)
    
    return '.'.join(new_items)

def normalize_name(name: str | None, prefix: str | None = None, suffix: str | None = None):
    parts = [p.strip() for p in (prefix, name, suffix) if p and p.strip()]
    if not parts:
        return ""

    result = ".".join(parts)
    result = re.sub(r'\.{2,}', '.', result)
    return collapse_name(result)

class GpgError(ZPError):
    """GPG operation error."""
    _prefix = "gpg"


class CompileError(ZPError):
    """Compilation error."""
    _prefix = "compile"


class PipelineError(ZPError):
    """Pipeline execution error."""
    _prefix = "pipeline"
