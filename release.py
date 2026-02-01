import sys
import os
import argparse
from pathlib import Path

# Add scripts directory to Python path
scripts_dir = Path(__file__).parent
sys.path.insert(0, str(scripts_dir))

from release_tool.release import run_release

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Release tool for Zenodo project")
    parser.add_argument(
        '--work-dir',
        type=str,
        default=None,
        help='Working directory (default: current directory)'
    )
    parser.add_argument(
        '--safeguard-validation-level',
        type=str,
        default="strict",
        help='Select the level of safeguard validation for prompt (strict, light, danger)'
    )
    args = parser.parse_args()
    
    # Change to working directory if specified
    if args.work_dir:
        os.chdir(args.work_dir)
    
    run_release(
        safeguard_validation_level=args.safeguard_validation_level.lower()
    )