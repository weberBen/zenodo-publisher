"""Static validation of glob pattern overlaps.

Detects if two pattern paths (with resolved template variables) could
match the same file, without touching the filesystem.

Splits paths into segments and uses interegular (FSM intersection via
fnmatch→regex conversion) to check if any pair of patterns can intersect.
"""

import fnmatch
from pathlib import PurePosixPath

from interegular import parse_pattern

from .env import ConfigError


def _segment_regex(pattern_segment: str) -> str:
    """Convert a single glob segment (e.g. '*.json') to a regex string.

    fnmatch.translate produces (?s:PATTERN)\\Z — strip the wrapper
    since interegular handles raw regex only.
    """
    raw = fnmatch.translate(pattern_segment)
    # Python 3.10+: (?s:PATTERN)\Z
    if raw.startswith("(?s:") and raw.endswith(")\\Z"):
        return raw[4:-3]
    if raw.endswith("\\Z"):
        return raw[:-2]
    return raw


def _segments_can_overlap(seg_a: str, seg_b: str) -> bool:
    """Check if two glob segments can match the same filename.

    Uses interegular (FSM intersection) for exact result.
    """
    if seg_a == seg_b:
        return True

    re_a = _segment_regex(seg_a)
    re_b = _segment_regex(seg_b)

    fsm_a = parse_pattern(re_a).to_fsm()
    fsm_b = parse_pattern(re_b).to_fsm()
    return not (fsm_a & fsm_b).empty()


def _normalize_path(pattern: str) -> str:
    """Normalize a pattern path (resolve .., remove trailing /)."""
    parts = PurePosixPath(pattern).parts
    normalized = []
    for part in parts:
        if part == "..":
            if normalized and normalized[-1] != "..":
                normalized.pop()
            else:
                normalized.append(part)
        elif part != ".":
            normalized.append(part)
    return "/".join(normalized) if normalized else "."


def patterns_overlap(pattern_a: str, pattern_b: str) -> bool:
    """Check if two resolved pattern paths can match the same file.

    Patterns are split into path segments and compared segment by segment.
    Both patterns must have the same number of segments (after normalization)
    and each corresponding segment pair must be able to overlap.

    Special case: if one pattern is a directory prefix of another
    (e.g. 'dir' vs 'dir/file.json'), the shorter one could match files
    inside the directory, so they overlap.
    """
    norm_a = _normalize_path(pattern_a)
    norm_b = _normalize_path(pattern_b)

    parts_a = norm_a.split("/")
    parts_b = norm_b.split("/")

    # Same depth: compare segment by segment
    if len(parts_a) == len(parts_b):
        return all(
            _segments_can_overlap(sa, sb)
            for sa, sb in zip(parts_a, parts_b)
        )

    # Different depth: check if the shorter is a directory prefix
    shorter, longer = (parts_a, parts_b) if len(parts_a) < len(parts_b) else (parts_b, parts_a)
    for sa, sb in zip(shorter, longer[:len(shorter)]):
        if not _segments_can_overlap(sa, sb):
            return False
    return True
