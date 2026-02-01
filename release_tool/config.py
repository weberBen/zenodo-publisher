"""Configuration management for the release tool."""

import os
from pathlib import Path
from typing import Optional


def find_project_root(start_path: Optional[Path] = None) -> Path:
    """
    Find the project root by looking for .git directory.

    Args:
        start_path: Starting path for search (default: current working directory)

    Returns:
        Path to project root

    Raises:
        RuntimeError: If project root cannot be found
    """
    current = start_path or Path.cwd()

    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent

    raise RuntimeError("Cannot find project root (no .git directory found)")


class NotInitializedError(Exception):
    """Project not initialized for Zenodo publisher."""
    pass


def load_env(project_root: Path) -> dict[str, str]:
    """
    Load environment variables from .zenodo.env file.

    Args:
        project_root: Path to project root

    Returns:
        Dictionary of environment variables

    Raises:
        NotInitializedError: If .zenodo.env file doesn't exist
    """
    env_file = project_root / ".zenodo.env"

    if not env_file.exists():
        raise NotInitializedError(
            f"Project not initialized for Zenodo publisher.\n"
            f"Missing: {env_file}\n"
        )

    env_vars = {}
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, _, value = line.partition("=")
                env_vars[key.strip()] = value.strip().strip('"').strip("'")

    return env_vars


class Config:
    """Configuration for release tool."""

    def __init__(self):
        self.project_root = find_project_root()
        self.env_vars = load_env(self.project_root)
        self.main_branch = self.env_vars.get("MAIN_BRANCH", "main")

        # Get LaTeX directory from env or use default
        latex_dir_str = self.env_vars.get("LATEX_DIR", "")
        self.latex_dir = self.project_root / latex_dir_str

        # Validate LaTeX directory exists
        if not self.latex_dir.exists():
            raise FileNotFoundError(
                f"LaTeX directory not found: {self.latex_dir}\n"
                f"Check LATEX_DIR in .env file"
            )
        
        # Archive configuration
        # ARCHIVE_TYPES: comma-separated list of what to archive (pdf, project)
        archive_types_str = self.env_vars.get("ARCHIVE_TYPES", "pdf")
        self.archive_types = [t.strip() for t in archive_types_str.split(",") if t.strip()]

        # PERSIST_TYPES: comma-separated list of what to persist to archive_dir (pdf, project)
        # Items not in this list will be created as temp files
        persist_types_str = self.env_vars.get("PERSIST_TYPES", "pdf")
        self.persist_types = [t.strip() for t in persist_types_str.split(",") if t.strip()]

        self.archive_dir = Path(self.env_vars.get("ARCHIVE_DIR", "")) if self.env_vars.get("ARCHIVE_DIR") else None
        self.pdf_base_name = self.env_vars.get("PDF_BASE_NAME", "main.pdf").replace(".pdf", "")
        self.base_name = self.env_vars.get("BASE_NAME", "")
        if not self.base_name:
            raise ValueError(
                "BASE_NAME not set in .env file\n"
                "This is used for naming the PDF file"
            )
        
        self.publisher_type = self.env_vars.get("PUBLISHER_TYPE", None)
        # Zenodo configuration (optional - only needed if publishing to Zenodo)
        self.zenodo_token = self.env_vars.get("ZENODO_TOKEN", "")
        self.zenodo_concept_doi = self.env_vars.get("ZENODO_CONCEPT_DOI", "")
        self.zenodo_api_url = self.env_vars.get(
            "ZENODO_API_URL",
            "https://zenodo.org/api"
        )
        # Publication date (optional, defaults to current UTC date if not set)
        self.publication_date = self.env_vars.get("PUBLICATION_DATE", None)

    def has_zenodo_config(self) -> bool:
        """Check if Zenodo configuration is complete."""
        return (self.publisher_type is not None)

    def __repr__(self) -> str:
        return (
            f"Config(project_root={self.project_root}, "
            f"main_branch={self.main_branch}, "
            f"latex_dir={self.latex_dir}, "
            f"base_name={self.base_name})"
        )
