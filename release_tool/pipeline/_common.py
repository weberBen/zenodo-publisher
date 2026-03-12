"""Shared pipeline utilities."""

from .. import output

RED_UNDERLINE = "\033[91;4m"
RESET = "\033[0m"

def setup_pipeline(config, *, test=None):
    """Common pipeline startup: setup output, print project info."""
    test_mode = test is not None
    output.setup(config.project_name_prefix, config.debug,
                 test_mode=test_mode, test_config=test)
    if config.config_path_overrided:
        output.warn("Config override: using '{path}' instead of repo config",
                     path=config.config_path, name="config_path_overrided")
    if test_mode:
        output.warn("Running in test mode", name="test_mode")
    if config.project_root:
        output.info_ok("Project root: {project_root}", project_root=str(config.project_root), name="project_root")
        output.info_ok("Project root name: {project_root_name}",
                       project_root_name=f"{RED_UNDERLINE}{config.project_root.name}{RESET}",
                       name="project_root_name")
    else:
        output.warn("No local project root detected", name="no_project_root")

    output.info_ok("Project name: {project_name}", project_name=config.project_name_prefix, name="project_name_prefix")
    output.step_ok("Project configuration checked", name="config_checked")
