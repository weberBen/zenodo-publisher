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
    name = name.strip() if name else None
    if not name:
        return ""
    
    prefix = prefix.strip() if prefix else None
    if prefix:
        name = f"{prefix}.{name}"
    
    suffix = suffix.strip() if suffix else None
    if suffix:
        name = f"{name}.{suffix}"
    
    name = re.sub(r'\.{2,}', '.', name)
    name = collapse_name(name)
    
    return name

class GpgError(ZPError):
    """GPG operation error."""
    _prefix = "gpg"


class CompileError(ZPError):
    """Compilation error."""
    _prefix = "compile"


class PipelineError(ZPError):
    """Pipeline execution error."""
    _prefix = "pipeline"
