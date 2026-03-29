"""Generated files configuration: dataclasses and YAML parser.

Parses the `generated_files:` section of zenodo_config.yaml into a list
of FileConfigEntry dataclasses. Each entry declares a file to include in the
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
          destination:
            file: [zenodo, github]
            sig: []
      project:                      # PROJECT — git archive ZIP
        publishers:
          destination:
            file: [zenodo]
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
    """Routing config: destination[type_name] = list of platforms for that type.

    type_name is one of: "file" (the file itself), "sig" (its GPG signature),
    or a module name (e.g. "digicert_timestamp") for module-produced files.

    Example:
        destination:
          file: [zenodo, github]
          sig: [github]
          digicert_timestamp: []
    """
    destination: dict[str, list[str]] = field(default_factory=dict)

    def destinations_for(self, type_name: str) -> list[str]:
        """Return platforms for the given type_name. Empty list if absent."""
        return self.destination.get(type_name, [])


@dataclass
class IdentifierConfig:
    """Per-file alternate identifier for Zenodo metadata.

    The hash of `source` (the file itself or its signature) is formatted
    as "zp:///<filename>;{algo}:{hash}" and added as an alternate identifier on Zenodo.
    """
    use_as_alternate_identifier: bool = True
    source: str = "file"       # "file" = hash the file, "sig_file" = hash the signature


@dataclass
class ManifestInclusion:
    """What to include in the manifest JSON (only for MANIFEST entries).

    content:         dict mapping config_key → list of type keys to include.
                     Type keys follow the archive_types convention:
                       "file" = FILE/PROJECT/MANIFEST entries
                       "sig"  = SIG entries
                       "<module_name>" = MODULE_ENTRY entries from that module
                     None = include all non-sig files (default).
    commit_info:     git commit fields to embed (e.g. ["sha", "date_epoch"])
    zenodo_metadata: fields from .zenodo.json to embed (e.g. ["title", "creators"])
    """
    content: dict[str, list[str]] | None = None
    commit_info: list[str] = field(default_factory=lambda: ["sha", "date_epoch"])
    zenodo_metadata: list[str] = field(default_factory=list)


@dataclass
class FileConfigEntry:
    """Config-level entry from the generated_files YAML section.

    Attributes:
        key:             YAML key (e.g. "paper", "project", "manifest")
        type:            entry type (PATTERN / PROJECT / MANIFEST)
        parent_key:      key this entry was derived from (None for root entries)
        pattern:         resolved glob path (after template substitution)
        pattern_template: original pattern from YAML (before template substitution)
        rename:          rename file using project_name template
        sign:            per-file signing override (None = use global default)
        sign_mode:       per-file sign mode override (None = use global default)
        archive_types:   per-entry override of global archive.types policy (None = use global,
                         [] = archive nothing from this entry)
        publishers:      per-file publisher override (None = use global default)
        modules:         module_name -> per-file config override (presence = module active)
        identifier:      alternate identifier config for Zenodo
        manifest_config: manifest-specific config (MANIFEST only)
        resolved_paths:  populated at runtime after glob resolution
    """
    key: str
    type: FileEntryKind
    parent_key: str | None = None
    pattern: str | None = None
    pattern_template: str | None = None
    rename: bool = False
    sign: bool | None = None
    sign_mode: SignMode | None = None
    archive_types: list[str] | None = None
    publishers: PublisherDestinations | None = None
    modules: dict[str, dict] = field(default_factory=dict)
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
            f"Keys ending with '_sig' are reserved for signature references: {bad}",
            name="generated_files.reserved_key",
        )


def _validate_manifest_refs(manifest_entry: FileConfigEntry, all_keys: set[str]) -> None:
    """Check that manifest.content keys exist in generated_files."""
    if manifest_entry.manifest_config is None or manifest_entry.manifest_config.content is None:
        return
    for key in manifest_entry.manifest_config.content:
        if key not in all_keys:
            raise ConfigError(
                f"Manifest content references unknown key '{key}'. "
                f"Available keys: {sorted(all_keys)}",
                name="generated_files.manifest.unknown_ref",
            )


def _validate_identifier(entry: FileConfigEntry) -> None:
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
            f"got '{entry.identifier.source}'",
            name="generated_files.identifier.invalid_source",
        )
    if entry.identifier.source == "sig_file" and entry.sign is False:
        raise ConfigError(
            f"'{entry.key}' has identifier.source=sig_file but sign=false",
            name="generated_files.identifier.sign.need",
        )
    if entry.type == FileEntryKind.PATTERN and entry.pattern and "*" in entry.pattern:
        raise ConfigError(
            f"'{entry.key}' uses glob pattern '*' and has identifier config. "
            f"Glob patterns matching multiple files can't be used as identifier source.",
            name="generated_files.identifier.glob_conflict",
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
                    f"and '{entry_b.key}' ({entry_b.pattern_template})",
                    name="generated_files.pattern_overlap",
                )

# ---------------------------------------------------------------------------
# Parsers — YAML dict → dataclasses
# ---------------------------------------------------------------------------

def _parse_publishers(raw: Any) -> PublisherDestinations | None:
    """Parse a publishers block from YAML.

    Expected format:
        publishers:
          destination:
            file: [zenodo, github]
            sig: []
            my_module: [zenodo]

    Returns None if raw is empty/absent (caller uses global default).
    """
    if not raw or not isinstance(raw, dict):
        return None
    dest_raw = raw.get("destination")
    if not dest_raw or not isinstance(dest_raw, dict):
        return None
    destination = {}
    for type_name, platforms in dest_raw.items():
        if isinstance(platforms, str):
            platforms = [platforms]
        if not isinstance(platforms, list):
            raise ConfigError(
                f"publishers.destination.{type_name} must be a list of platforms",
                name="generated_files.publishers.invalid_dest",
            )
        for p in platforms:
            if p not in VALID_DESTINATIONS:
                raise ConfigError(
                    f"Unknown destination '{p}'. Valid: {VALID_DESTINATIONS}",
                    name="generated_files.publishers.invalid_platform",
                )
        destination[type_name] = platforms
    return PublisherDestinations(destination=destination)


def _parse_file_modules(raw: Any) -> dict[str, dict]:
    """Parse per-file modules override block from YAML.

    Returns dict mapping module_name -> per-file config dict.
    An empty dict {} means 'use global config as-is'.
    """
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(
            "'modules' in generated_files entry must be a YAML mapping",
            name="generated_files.modules.invalid_format",
        )
    return {name: (cfg if isinstance(cfg, dict) else {}) for name, cfg in raw.items()}


def _parse_identifier(raw: Any) -> IdentifierConfig | None:
    """Parse an identifier block from YAML. Returns None if absent."""
    if not raw or not isinstance(raw, dict):
        return None
    return IdentifierConfig(
        use_as_alternate_identifier=raw.get("use_as_alternate_identifier", True),
        source=raw.get("source", "file"),
    )


def _parse_sign_mode(raw: Any) -> SignMode | None:
    """Parse optional per-file sign_mode override. Returns None if absent."""
    if raw is None:
        return None
    try:
        return SignMode(str(raw))
    except ValueError:
        valid = [m.value for m in SignMode]
        raise ConfigError(f"Invalid sign_mode '{raw}'. Valid: {', '.join(valid)}", name="generated_files.sign.invalid_mode")


def _parse_manifest_config(raw: dict) -> ManifestInclusion:
    """Parse manifest-specific config (content, commit_info, zenodo_metadata)."""
    raw_content = raw.get("content")
    content: dict[str, list[str]] | None = None
    if raw_content is not None:
        if not isinstance(raw_content, dict):
            raise ConfigError(
                "manifest.content must be a YAML mapping (key: [types])",
                name="generated_files.manifest.content.invalid_format",
            )
        content = {}
        for key, types in raw_content.items():
            if isinstance(types, str):
                types = [types]
            if not isinstance(types, list):
                raise ConfigError(
                    f"manifest.content.{key} must be a list of type keys",
                    name="generated_files.manifest.content.invalid_types",
                )
            content[key] = types
    return ManifestInclusion(
        content=content,
        commit_info=raw.get("commit_info", ["sha", "date_epoch"]),
        zenodo_metadata=raw.get("zenodo_metadata", []),
    )


def _parse_pattern_entry(key: str, raw: dict) -> FileConfigEntry:
    """Parse a user-defined PATTERN entry. Requires 'pattern' key."""
    if "pattern" not in raw:
        raise ConfigError(
            f"generated_files.{key}: 'pattern' is required for non-special keys",
            name="generated_files.missing_pattern",
        )
    _validate_pattern_template(raw["pattern"])
    return FileConfigEntry(
        key=key,
        type=FileEntryKind.PATTERN,
        pattern=raw["pattern"],
        pattern_template=raw["pattern"],
        rename=raw.get("rename", False),
        sign=raw.get("sign"),
        sign_mode=_parse_sign_mode(raw.get("sign_mode")),
        archive_types=raw.get("archive_types"),
        publishers=_parse_publishers(raw.get("publishers")),
        modules=_parse_file_modules(raw.get("modules")),
        identifier=_parse_identifier(raw.get("identifier")),
    )


def _parse_project_entry(raw: dict | None) -> FileConfigEntry:
    """Parse the reserved 'project' entry (git archive ZIP)."""
    raw = raw or {}
    return FileConfigEntry(
        key="project",
        type=FileEntryKind.PROJECT,
        sign=raw.get("sign"),
        sign_mode=_parse_sign_mode(raw.get("sign_mode")),
        archive_types=raw.get("archive_types"),
        publishers=_parse_publishers(raw.get("publishers")),
        modules=_parse_file_modules(raw.get("modules")),
        identifier=_parse_identifier(raw.get("identifier")),
    )


def _parse_manifest_entry(raw: dict | None) -> FileConfigEntry:
    """Parse the reserved 'manifest' entry (JSON manifest)."""
    raw = raw or {}
    return FileConfigEntry(
        key="manifest",
        type=FileEntryKind.MANIFEST,
        sign=raw.get("sign"),
        sign_mode=_parse_sign_mode(raw.get("sign_mode")),
        archive_types=raw.get("archive_types"),
        publishers=_parse_publishers(raw.get("publishers")),
        modules=_parse_file_modules(raw.get("modules")),
        identifier=_parse_identifier(raw.get("identifier")),
        manifest_config=_parse_manifest_config(raw),
    )


def parse_generated_files(raw: Any) -> list[FileConfigEntry]:
    """Parse the 'generated_files' section from YAML into a list of FileConfigEntry.

    Processing order:
      1. Validate no key ends with '_sig' (reserved suffix)
      2. Parse each key into a FileConfigEntry (dispatch by type)
      3. Validate manifest file references exist
      4. Validate identifier constraints per entry
    """
    if not raw:
        return []
    if not isinstance(raw, dict):
        raise ConfigError("'generated_files' must be a YAML mapping", name="generated_files.invalid_format")

    keys = list(raw.keys())
    _validate_no_sig_keys(keys)

    entries: list[FileConfigEntry] = []
    for key, value in raw.items():
        value = value or {}
        if not isinstance(value, dict):
            raise ConfigError(f"generated_files.{key} must be a YAML mapping", name=f"generated_files.invalid_entry.{key}")

        if key == "project":
            entries.append(_parse_project_entry(value))
        elif key == "manifest":
            entries.append(_parse_manifest_entry(value))
        else:
            entries.append(_parse_pattern_entry(key, value))

    # Cross-entry validation
    all_keys = {e.key for e in entries}
    for entry in entries:
        if entry.type == FileEntryKind.MANIFEST:
            _validate_manifest_refs(entry, all_keys)
        _validate_identifier(entry)

    return entries
