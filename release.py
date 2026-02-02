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
        '--prompt-validation-level',
        type=str,
        default="strict",
        help='Select the level of safeguard validation for prompt (strict, light)'
    )
    parser.add_argument(
        '--force_zenodo_update',
        action='store_true',
        help='Force update to zenodo even if up to date'
    )
    args = parser.parse_args()
    
    # Change to working directory if specified
    if args.work_dir:
        os.chdir(args.work_dir)
    
    run_release(
        prompt_validation_level=args.prompt_validation_level.lower(),
        force_zenodo_update=args.force_zenodo_update
    )