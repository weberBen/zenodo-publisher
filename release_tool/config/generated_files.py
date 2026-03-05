"""Generated files configuration: dataclasses and YAML parser.

Parses the `generated_files:` section of zenodo_config.yaml into a list
of FileEntry dataclasses. Each entry declares a file to include in the
release, with per-file signing, renaming, publishing destinations, and
identifier options.

Three kinds of entries exist (FileEntryKind):
  - PATTERN : a compiled file matched by glob in compile_dir (e.g. "main.pdf")
  - PROJECT : the git archive ZIP of the repository (reserved key "project")
  - MANIFEST: a JSON manifest summarizing the release (reserved key "manifest")

Example YAML:
    generated_files:
      paper:                        # PATTERN — matched in compile_dir
        pattern: "main.pdf"
        rename: true                # rename using project_name template
        sign: true                  # override global signing.sign
        publishers:
          file_destination: [zenodo, github]
      project:                      # PROJECT — git archive ZIP
        publishers:
          file_destination: [zenodo]
      manifest:                     # MANIFEST — JSON with hashes + metadata
        files: [paper, project]     # which entries to include
        sign: true
        identifier:
          use_as_alternate_identifier: true
          source: file              # hash of this file becomes Zenodo identifier

Convention: the suffix '_sig' is reserved for referencing signature files
in configs (e.g. "paper_sig" in manifest.files). User keys must not end
with '_sig'.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .env import ConfigError
from .signing import SignMode
from .transform_common import _validate_pattern_template
from .pattern_overlap import patterns_overlap

# ---------------------------------------------------------------------------
# Enums & constants
# ---------------------------------------------------------------------------

class FileEntryKind(Enum):
    """Type of generated file entry."""
    PATTERN = "pattern"     # compiled file matched by glob in compile_dir
    PROJECT = "project"     # git archive ZIP of the repo (reserved key)
    MANIFEST = "manifest"   # JSON manifest of the release (reserved key)


SPECIAL_KEYS = {"project", "manifest"}
VALID_DESTINATIONS = {"zenodo", "github"}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PublisherDestinations:
    """Where to upload the file and its signature.

    file_destination: platforms receiving the file itself (e.g. ["zenodo", "github"])
    sig_destination:  platforms receiving the .asc/.sig signature (default: none)
    """
    file_destination: list[str] = field(default_factory=lambda: ["zenodo"])
    sig_destination: list[str] = field(default_factory=list)


@dataclass
class IdentifierConfig:
    """Per-file alternate identifier for Zenodo metadata.

    The hash of `source` (the file itself or its signature) is formatted
    as "{prefix}{hash}" and added as an alternate identifier on Zenodo.
    """
    use_as_alternate_identifier: bool = True
    source: str = "file"       # "file" = hash the file, "sig_file" = hash the signature
    prefix: str = ""


@dataclass
class ManifestInclusion:
    """What to include in the manifest JSON (only for MANIFEST entries).

    files:           list of entry keys whose hashes appear in the manifest
                     (append '_sig' to reference a signature, e.g. "paper_sig")
    commit_info:     git commit fields to embed (e.g. ["sha", "date_epoch"])
    zenodo_metadata: fields from .zenodo.json to embed (e.g. ["title", "creators"])
    """
    files: list[str] = field(default_factory=list)
    commit_info: list[str] = field(default_factory=lambda: ["sha", "date_epoch"])
    zenodo_metadata: list[str] = field(default_factory=list)


@dataclass
class FileEntry:
    """A single entry in the generated_files section.

    Attributes:
        key:             YAML key (e.g. "paper", "project", "manifest")
        kind:            entry type (PATTERN / PROJECT / MANIFEST)
        pattern:          resolved glob path (after template substitution)
        pattern_template: original pattern from YAML (before template substitution)
        rename:          rename file using project_name template
        sign:            per-file signing override (None = use global default)
        sign_mode:       per-file sign mode override (None = use global default)
        archive:         include this file in the release
        publishers:      where to upload file and signature
        identifier:      alternate identifier config for Zenodo
        manifest_config: manifest-specific config (MANIFEST only)
        resolved_paths:  populated at runtime after glob resolution
    """
    key: str
    kind: FileEntryKind
    pattern: str | None = None
    pattern_template: str | None = None
    rename: bool = False
    sign: bool | None = None
    sign_mode: SignMode | None = None
    archive: bool = True
    publishers: PublisherDestinations = field(default_factory=PublisherDestinations)
    identifier: IdentifierConfig | None = None
    manifest_config: ManifestInclusion | None = None

    # Resolved at runtime (not from YAML)
    resolved_paths: list[Path] = field(default_factory=list)

    def effective_sign(self, global_sign: bool) -> bool:
        """Return whether this entry should be signed (per-file or global)."""
        return self.sign if self.sign is not None else global_sign

    def effective_sign_mode(self, global_sign_mode: SignMode) -> SignMode:
        """Return the sign mode for this entry (per-file or global)."""
        return self.sign_mode if self.sign_mode is not None else global_sign_mode


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def _validate_no_sig_keys(keys: list[str]) -> None:
    """No user key can end with _sig (reserved for signature references)."""
    bad = [k for k in keys if k.endswith("_sig")]
    if bad:
        raise ConfigError(
            f"Keys ending with '_sig' are reserved for signature references: {bad}"
        )


def _validate_destinations(pubs: PublisherDestinations) -> None:
    """Check that all destinations are known (zenodo, github)."""
    for dest in pubs.file_destination:
        if dest not in VALID_DESTINATIONS:
            raise ConfigError(f"Unknown destination '{dest}'. Valid: {VALID_DESTINATIONS}")
    for dest in pubs.sig_destination:
        if dest not in VALID_DESTINATIONS:
            raise ConfigError(f"Unknown destination '{dest}'. Valid: {VALID_DESTINATIONS}")


def _validate_manifest_refs(manifest_entry: FileEntry, all_keys: set[str]) -> None:
    """Check that manifest.files references exist (with _sig suffix allowed)."""
    if manifest_entry.manifest_config is None:
        return
    for ref in manifest_entry.manifest_config.files:
        base = ref.removesuffix("_sig")
        if base not in all_keys:
            raise ConfigError(
                f"Manifest references unknown key '{ref}'. "
                f"Available keys: {sorted(all_keys)}"
            )


def _validate_identifier(entry: FileEntry) -> None:
    """Validate identifier config constraints.

    Rules:
      - source must be "file" or "sig_file"
      - source=sig_file requires sign=true (need a signature to hash)
      - glob patterns with '*' can't be identifier source (ambiguous multi-match)
    """
    if entry.identifier is None:
        return
    if entry.identifier.source not in ("file", "sig_file"):
        raise ConfigError(
            f"'{entry.key}': identifier.source must be 'file' or 'sig_file', "
            f"got '{entry.identifier.source}'"
        )
    if entry.identifier.source == "sig_file" and entry.sign is False:
        raise ConfigError(
            f"'{entry.key}' has identifier.source=sig_file but sign=false"
        )
    if entry.kind == FileEntryKind.PATTERN and entry.pattern and "*" in entry.pattern:
        raise ConfigError(
            f"'{entry.key}' uses glob pattern '*' and has identifier config. "
            f"Glob patterns matching multiple files can't be used as identifier source."
        )

def validate_no_pattern_overlap(entries) -> None:
    """Check that no two FileEntry patterns can match the same files.

    Called after template resolution (entry.pattern contains resolved paths).
    Raises ConfigError if overlapping patterns are found.
    """
    pattern_entries = [e for e in entries if e.pattern is not None]
    if len(pattern_entries) < 2:
        return

    for i, entry_a in enumerate(pattern_entries):
        for entry_b in pattern_entries[i + 1:]:
            if patterns_overlap(entry_a.pattern, entry_b.pattern):
                raise ConfigError(
                    f"Patterns may overlap: "
                    f"'{entry_a.key}' ({entry_a.pattern_template}) "
                    f"and '{entry_b.key}' ({entry_b.pattern_template})"
                )

# ---------------------------------------------------------------------------
# Parsers — YAML dict → dataclasses
# ---------------------------------------------------------------------------

def _parse_publishers(raw: Any) -> PublisherDestinations:
    """Parse a publishers block from YAML. Accepts string or list."""
    if not raw or not isinstance(raw, dict):
        return PublisherDestinations()
    pubs = PublisherDestinations(
        file_destination=raw.get("file_destination", ["zenodo"]),
        sig_destination=raw.get("sig_destination", []),
    )
    # Accept single string as shorthand: "zenodo" → ["zenodo"]
    if isinstance(pubs.file_destination, str):
        pubs.file_destination = [pubs.file_destination]
    if isinstance(pubs.sig_destination, str):
        pubs.sig_destination = [pubs.sig_destination]
    _validate_destinations(pubs)
    return pubs


def _parse_identifier(raw: Any) -> IdentifierConfig | None:
    """Parse an identifier block from YAML. Returns None if absent."""
    if not raw or not isinstance(raw, dict):
        return None
    return IdentifierConfig(
        use_as_alternate_identifier=raw.get("use_as_alternate_identifier", True),
        source=raw.get("source", "file"),
        prefix=str(raw.get("prefix", "")),
    )


def _parse_sign_mode(raw: Any) -> SignMode | None:
    """Parse optional per-file sign_mode override. Returns None if absent."""
    if raw is None:
        return None
    try:
        return SignMode(str(raw))
    except ValueError:
        valid = [m.value for m in SignMode]
        raise ConfigError(f"Invalid sign_mode '{raw}'. Valid: {', '.join(valid)}")


def _parse_manifest_config(raw: dict) -> ManifestInclusion:
    """Parse manifest-specific config (files, commit_info, zenodo_metadata)."""
    return ManifestInclusion(
        files=raw.get("files", []),
        commit_info=raw.get("commit_info", ["sha", "date_epoch"]),
        zenodo_metadata=raw.get("zenodo_metadata", []),
    )


def _parse_pattern_entry(key: str, raw: dict) -> FileEntry:
    """Parse a user-defined PATTERN entry. Requires 'pattern' key."""
    if "pattern" not in raw:
        raise ConfigError(
            f"generated_files.{key}: 'pattern' is required for non-special keys"
        )
    _validate_pattern_template(raw["pattern"])
    return FileEntry(
        key=key,
        kind=FileEntryKind.PATTERN,
        pattern=raw["pattern"],
        pattern_template=raw["pattern"],
        rename=raw.get("rename", False),
        sign=raw.get("sign"),
        sign_mode=_parse_sign_mode(raw.get("sign_mode")),
        archive=raw.get("archive", True),
        publishers=_parse_publishers(raw.get("publishers")),
        identifier=_parse_identifier(raw.get("identifier")),
    )


def _parse_project_entry(raw: dict | None) -> FileEntry:
    """Parse the reserved 'project' entry (git archive ZIP)."""
    raw = raw or {}
    return FileEntry(
        key="project",
        kind=FileEntryKind.PROJECT,
        sign=raw.get("sign"),
        sign_mode=_parse_sign_mode(raw.get("sign_mode")),
        archive=raw.get("archive", True),
        publishers=_parse_publishers(raw.get("publishers")),
        identifier=_parse_identifier(raw.get("identifier")),
    )


def _parse_manifest_entry(raw: dict | None) -> FileEntry:
    """Parse the reserved 'manifest' entry (JSON manifest)."""
    raw = raw or {}
    return FileEntry(
        key="manifest",
        kind=FileEntryKind.MANIFEST,
        sign=raw.get("sign"),
        sign_mode=_parse_sign_mode(raw.get("sign_mode")),
        archive=raw.get("archive", True),
        publishers=_parse_publishers(raw.get("publishers")),
        identifier=_parse_identifier(raw.get("identifier")),
        manifest_config=_parse_manifest_config(raw),
    )


def parse_generated_files(raw: Any) -> list[FileEntry]:
    """Parse the 'generated_files' section from YAML into a list of FileEntry.

    Processing order:
      1. Validate no key ends with '_sig' (reserved suffix)
      2. Parse each key into a FileEntry (dispatch by kind)
      3. Validate manifest file references exist
      4. Validate identifier constraints per entry
    """
    if not raw:
        return []
    if not isinstance(raw, dict):
        raise ConfigError("'generated_files' must be a YAML mapping")

    keys = list(raw.keys())
    _validate_no_sig_keys(keys)

    entries: list[FileEntry] = []
    for key, value in raw.items():
        value = value or {}
        if not isinstance(value, dict):
            raise ConfigError(f"generated_files.{key} must be a YAML mapping")

        if key == "project":
            entries.append(_parse_project_entry(value))
        elif key == "manifest":
            entries.append(_parse_manifest_entry(value))
        else:
            entries.append(_parse_pattern_entry(key, value))

    # Cross-entry validation
    all_keys = {e.key for e in entries}
    for entry in entries:
        if entry.kind == FileEntryKind.MANIFEST:
            _validate_manifest_refs(entry, all_keys)
        _validate_identifier(entry)

    return entries
