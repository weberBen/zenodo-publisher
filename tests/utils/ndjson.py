"""NDJSON stream parser for zp --test-mode output."""

import json


def _name_matches(event_name: str | None, query: str) -> bool:
    """Check if event_name matches query with scope support.

    Exact match or prefix match with dot separator:
      "config_error.git.no_root" matches "config_error", "config_error.git",
      and "config_error.git.no_root".
    """
    if event_name is None:
        return False
    if event_name == query:
        return True
    return event_name.startswith(query + ".")


def parse_stream(stdout: str) -> list[dict]:
    """Parse all NDJSON lines from stdout."""
    events = []
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # skip non-JSON lines (e.g. raw error output)
    return events


def find_data(events: list[dict], code: str):
    """Find the value of a data event by code. Returns None if not found."""
    for e in events:
        if e.get("type") == "data" and e.get("code") == code:
            return e["value"]
    return None


def find_all_data(events: list[dict], code: str) -> list:
    """Find all values of data events with the given code."""
    return [
        e["value"] for e in events
        if e.get("type") == "data" and e.get("code") == code
    ]


def find_errors(events: list[dict]) -> list[dict]:
    """Find all error and fatal events."""
    return [e for e in events if e["type"] in ("error", "fatal")]


def find_warnings(events: list[dict]) -> list[dict]:
    """Find all warning events."""
    return [e for e in events if e["type"] == "warn"]


def has_step_ok(events: list[dict], name: str) -> bool:
    """Check if a step completed successfully."""
    return any(
        e["type"] == "step_ok" and _name_matches(e.get("name"), name)
        for e in events
    )


def filter_by_type(events: list[dict], *types: str) -> list[dict]:
    """Filter events by type(s)."""
    return [e for e in events if e["type"] in types]


def find_by_name(events: list[dict], name: str) -> dict | None:
    """Find the first event matching name (supports scope prefixes)."""
    for e in events:
        if _name_matches(e.get("name"), name):
            return e
    return None


def find_all_by_name(events: list[dict], name: str) -> list[dict]:
    """Find all events matching name (supports scope prefixes)."""
    return [e for e in events if _name_matches(e.get("name"), name)]


# ---------------------------------------------------------------------------
# Prompt verification
# ---------------------------------------------------------------------------

def get_prompt_names(events: list[dict]) -> set[str]:
    """Extract names of all prompt/confirm events triggered during a run."""
    return {
        e["name"] for e in events
        if e.get("type") in ("prompt", "confirm") and "name" in e
    }


def verify_prompts(events: list[dict], expected_prompts: set[str]):
    """Assert that triggered prompts match expected prompts exactly.

    Raises AssertionError if there are extra or missing prompts.
    """
    triggered = get_prompt_names(events)
    missing = expected_prompts - triggered
    extra = triggered - expected_prompts
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing={missing}")
        if extra:
            parts.append(f"extra={extra}")
        raise AssertionError(f"Prompt mismatch: {', '.join(parts)}")
