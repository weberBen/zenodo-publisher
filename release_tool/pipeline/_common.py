"""Shared pipeline utilities."""

from .. import output

RED_UNDERLINE = "\033[91;4m"
RESET = "\033[0m"

def setup_pipeline(project_name, debug=False, project_root=None):
    """Common pipeline startup: setup output, print project info."""
    output.setup(project_name, debug)
    if project_root:
        output.info_ok(f"Project root: {project_root}")
        output.info_ok(f"Project root name: {RED_UNDERLINE}{project_root.name}{RESET}")
    else:
        output.warn(f"No local project root detected")
    
    output.info_ok(f"Project name: {project_name}")
