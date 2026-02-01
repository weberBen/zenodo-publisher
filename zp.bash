#!/bin/bash

# Path of the tool root dir
SCRIPT_PATH="$(readlink -f "$0")"
ZP_DIR="$(dirname "$SCRIPT_PATH")"

WORK_DIR="$(pwd)"

# Start the tool with uv env of the tool directory
exec uv --directory "$ZP_DIR" run python "$ZP_DIR/release.py" --work-dir "$WORK_DIR" "$@"
