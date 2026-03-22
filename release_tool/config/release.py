"""Release configuration: ReleaseConfig + RELEASE_OPTIONS."""

from .schema import ConfigOption
from .transform_common import _resolve_optional_path, _TEMPLATE_VAR_RE
from .transform_release import (
    _resolve_compile_dir,
    _dedup_make_args,
)
from .common import COMMON_OPTIONS, CommonConfig
from .env import ConfigError
from .signing import parse_signing_config, SigningConfig
from .generated_files import parse_generated_files, FileEntry, FileEntryKind
from .generated_files import validate_no_pattern_overlap


# ---------------------------------------------------------------------------
# Release-specific options (simple scalars only — complex structures
# like generated_files and signing are parsed from YAML directly)
# ---------------------------------------------------------------------------

RELEASE_OPTIONS: list[ConfigOption] = [
    ConfigOption("main_branch", env_key=None,
                 yaml_path="main_branch", default="main",
                 help="Git main branch name"),
    ConfigOption("compile_enabled", env_key=None,
                 yaml_path="compile.enabled",
                 type="bool", default=True,
                 help="Enable project compilation"),
    ConfigOption("compile_dir", env_key=None,
                 yaml_path="compile.dir", default="",
                 transform=_resolve_compile_dir,
                 help="Compile directory (relative to project root)"),
    ConfigOption("make_args", env_key=None,
                 yaml_path="compile.make_args",
                 type="list", default="",
                 transform=_dedup_make_args,
                 help="Extra args passed to make (e.g. -j4,VERBOSE=1)"),

    # Zenodo — token stays in env, rest in YAML
    ConfigOption("zenodo_token", env_key="ZENODO_TOKEN",
                 default="", cli=False,
                 help="Zenodo API token"),
    ConfigOption("zenodo_concept_doi", env_key="ZENODO_CONCEPT_DOI",
                 yaml_path="zenodo.concept_doi", default="",
                 help="Zenodo concept DOI"),
    ConfigOption("zenodo_api_url", env_key=None,
                 yaml_path="zenodo.api_url",
                 default="https://zenodo.org/api",
                 help="Zenodo API base URL"),
    ConfigOption("publication_date", env_key=None,
                 yaml_path="zenodo.publication_date", nullable=True,
                 help="Publication date (YYYY-MM-DD), defaults to today UTC"),
    ConfigOption("zenodo_force_update", env_key=None,
                 yaml_path="zenodo.force_update",
                 type="bool", default=False,
                 help="Force Zenodo update even if up to date"),

    # Archive persistence
    ConfigOption("archive_dir", env_key=None,
                 yaml_path="archive.dir", nullable=True,
                 transform=_resolve_optional_path,
                 help="Directory for persistent archives"),

    # Signing on/off (rest of signing config is in SigningConfig from YAML)
    ConfigOption("gpg_sign", env_key=None,
                 yaml_path="signing.sign",
                 type="bool", default=False,
                 help="Enable GPG signing"),

    # GitHub checks
    ConfigOption("check_gh_draft", env_key=None,
                 yaml_path="github.check_draft",
                 type="bool", default=False, cli=False,
                 help="Reject tags associated with draft releases (scans all releases via API)"),

    # Runtime options
    ConfigOption("prompt_validation_level", env_key=None,
                 yaml_path="prompt_validation_level",
                 default="light",
                 choices=["danger", "light", "normal", "secure"],
                 help="Prompt validation level: "
                      "danger (Enter to confirm), "
                      "light (y/yes), "
                      "normal (yes/no in full), "
                      "secure (type project root name)"),
]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_compile_dir(config) -> None:
    """Check that compile_dir exists if set."""
    if config.compile_dir and not config.compile_dir.exists():
        raise ConfigError(
            f"Compile directory not found: {config.compile_dir}",
            name="release.compile_dir.not_found",
        )


def validate_project_root(config) -> None:
    project_root = config.project_root
    if not project_root:
        raise ConfigError("No project root defined", name="release.no_project_root")
    if not project_root.exists():
        raise ConfigError(f"Invalid project root {project_root}", name="release.invalid_project_root")


def validate(config):
    validate_project_root(config)
    validate_compile_dir(config)


# ---------------------------------------------------------------------------
# ReleaseConfig
# ---------------------------------------------------------------------------

class ReleaseConfig(CommonConfig):
    """Configuration for the release command.

    Simple scalar options are handled by ConfigOption (auto-generated CLI).
    Complex structures (signing, generated_files) are parsed from YAML directly.
    """

    _options = COMMON_OPTIONS + RELEASE_OPTIONS
    _required: list[str] = []
    _cli_aliases: dict[str, str] = {"gpg_sign": "sign"}

    signing: SigningConfig
    generated_files: list[FileEntry]

    def __init__(self, project_root, yaml_config, env_vars, cli_overrides=None):
        super().__init__(project_root, yaml_config, env_vars, cli_overrides)

        # Parse complex structures from YAML
        self.signing = parse_signing_config(yaml_config.get("signing", {}))
        self.generated_files = parse_generated_files(
            yaml_config.get("generated_files", {}),
        )

        # Sync signing.sign from ConfigOption (handles CLI > YAML > default)
        self.signing.sign = self.gpg_sign

        # Resolve {compile_dir}, {project_root} in pattern templates
        self._resolve_pattern_templates()

        # Static check: no two patterns can match the same files
        validate_no_pattern_overlap(self.generated_files)

        validate(self)

    def _resolve_pattern_templates(self):
        """Resolve {var} in pattern entries using config values.

        {project_name} is NOT resolved here (not yet available),
        it is resolved later in _step_resolve_generated_files.
        """
        context = {}
        if self.compile_dir:
            context["compile_dir"] = str(self.compile_dir)
        if self.project_root:
            context["project_root"] = str(self.project_root)

        for entry in self.generated_files:
            if entry.pattern_template is None:
                continue
            found_vars = _TEMPLATE_VAR_RE.findall(entry.pattern_template)
            for var in found_vars:
                if var == "project_name":
                    continue  # resolved at runtime
                if var not in context:
                    raise ConfigError(
                        f"generated_files.{entry.key}: pattern uses "
                        f"'{{{var}}}' but {var} is not set",
                        name=f"release.pattern_unresolved_var.{entry.key}",
                    )
            # Resolve all available variables, leave {project_name} as-is
            entry.pattern = entry.pattern_template.format_map(
                {**context, "project_name": "{project_name}"}
            )

    def has_zenodo_config(self) -> bool:
        """Check if Zenodo configuration is complete."""
        return bool(self.zenodo_concept_doi)

    # --- Convenience properties for pipeline code ---

    @property
    def gpg_uid(self) -> str | None:
        return self.signing.gpg_uid

    @property
    def gpg_extra_args(self) -> list[str]:
        return self.signing.gpg_extra_args

    @property
    def sign_mode(self) -> str:
        return self.signing.sign_mode.value

    @property
    def sign_hash_algo(self) -> str:
        return self.signing.sign_hash_algo
