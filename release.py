# Wrapper to run without installing the package: python release.py
# Relative imports in release_tool/ prevent running cli.py directly.
# Installed usage: zp (or zenodo-publisher) via pyproject.toml entry points.
from release_tool.cli import main

if __name__ == "__main__":
    main()
