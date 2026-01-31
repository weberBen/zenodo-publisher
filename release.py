#!/usr/bin/env python3
"""
Release tool for PathThinker project.

This script automates the release process:
1. Builds LaTeX document
2. Checks git repository status
3. Creates and pushes release tags

Can be executed from anywhere in the project.
"""

import sys
from pathlib import Path

# Add scripts directory to Python path
scripts_dir = Path(__file__).parent
sys.path.insert(0, str(scripts_dir))

from release_tool.release import run_release


if __name__ == "__main__":
    run_release()
