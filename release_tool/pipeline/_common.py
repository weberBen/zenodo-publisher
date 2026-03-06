"""Shared pipeline utilities."""

from .. import output

RED_UNDERLINE = "\033[91;4m"
RESET = "\033[0m"

def setup_pipeline(project_name, debug=False, project_root=None, test_mode=False):
    """Common pipeline startup: setup output, print project info."""
    output.setup(project_name, debug, test_mode=test_mode)
    if test_mode:
        output.warn("Running in test mode", name="test_mode")
    if project_root:
        output.info_ok("Project root: {project_root}", project_root=str(project_root), name="project_root")
        output.info_ok("Project root name: {project_root_name}",
                       project_root_name=f"{RED_UNDERLINE}{project_root.name}{RESET}",
                       name="project_root_name")
    else:
        output.warn("No local project root detected", name="no_project_root")

    output.info_ok("Project name: {project_name}", project_name=project_name, name="project_name_prefix")
    output.step_ok("Project configuration checked", name="config_checked")
