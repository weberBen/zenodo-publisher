"""Central prompt declarations for the release tool.

All Prompt instances are declared here. Each has a unique name
registered in Prompt._registry at instantiation time.

Call init_prompts(config) once after config is loaded, before pipeline execution.
"""

from . import output

# Text prompts (no config dependency, instantiated immediately)
enter_tag = output.Prompt([output.TEXT], name="enter_tag")
release_title = output.Prompt([output.TEXT_OPTIONAL], name="release_title")
release_notes = output.Prompt([output.TEXT_OPTIONAL], name="release_notes")

# Confirm prompts (initialized by init_prompts() after config is loaded)
confirm_build: output.Prompt
confirm_publish: output.Prompt
confirm_github_overwrite: output.Prompt
confirm_delete_asset: output.Prompt
confirm_persist_overwrite: output.Prompt
confirm_gpg_key: output.Prompt
confirm_run_module: output.Prompt
confirm_resume: output.Prompt


def init_prompts(config):
    """Instantiate all confirm prompts after config is available."""
    global confirm_build, confirm_publish, confirm_github_overwrite
    global confirm_delete_asset, confirm_persist_overwrite, confirm_gpg_key, confirm_run_module
    global confirm_resume

    level_map = {"danger": "danger", "light": "light",
                 "normal": "complete", "secure": "complete"}
    level = level_map[config.prompt_validation_level]
    enter = (config.prompt_validation_level == "danger")
    secure = (config.project_root.name
              if config.prompt_validation_level == "secure" else None)

    confirm_build = output.Prompt(
        [output.YES, output.NO], name="confirm_build",
        level=level, enter_confirms=enter, secure_value=secure,
    )
    confirm_publish = output.Prompt(
        [output.YES, output.NO], name="confirm_publish",
        level=level, enter_confirms=enter, secure_value=secure,
    )
    confirm_github_overwrite = output.Prompt(
        [output.YES, output.NO], name="confirm_github_overwrite",
        level=level, enter_confirms=enter, secure_value=secure,
    )
    confirm_delete_asset = output.Prompt(
        [output.YES, output.NO], name="confirm_delete_asset",
        level=level, enter_confirms=enter, secure_value=secure,
    )
    confirm_persist_overwrite = output.Prompt(
        [output.YES, output.NO, output.YES_ALL, output.NO_ALL],
        name="confirm_persist_overwrite", level="light",
    )
    confirm_gpg_key = output.Prompt(
        [output.YES, output.NO], name="confirm_gpg_key", level="danger",
    )
    confirm_run_module = output.Prompt(
        [output.YES, output.NO], name="confirm_run_module",
        level=level, enter_confirms=enter, secure_value=secure,
    )
    confirm_resume = output.Prompt(
        [output.YES, output.NO], name="confirm_resume", level="light",
    )
