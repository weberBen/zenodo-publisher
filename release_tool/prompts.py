"""Central prompt declarations for the release tool.

All ConfirmPrompt instances are declared here. Each has a unique name
registered in output._prompt_registry at instantiation time.

Call init_prompts(config) once after config is loaded, before pipeline execution.
"""

from . import output

# Initialized by init_prompts() after config is loaded
confirm_build: output.ConfirmPrompt
confirm_publish: output.ConfirmPrompt
confirm_github_overwrite: output.ConfirmPrompt
confirm_persist_overwrite: output.ConfirmPrompt
confirm_gpg_key: output.ConfirmPrompt


def init_prompts(config):
    """Instantiate all ConfirmPrompt after config is available."""
    global confirm_build, confirm_publish, confirm_github_overwrite
    global confirm_persist_overwrite, confirm_gpg_key

    level_map = {"danger": "danger", "light": "light",
                 "normal": "complete", "secure": "complete"}
    level = level_map[config.prompt_validation_level]
    enter = (config.prompt_validation_level == "danger")
    secure = (config.project_root.name
              if config.prompt_validation_level == "secure" else None)

    confirm_build = output.ConfirmPrompt(
        [output.YES, output.NO], name="confirm_build",
        level=level, enter_confirms=enter, secure_value=secure,
    )
    confirm_publish = output.ConfirmPrompt(
        [output.YES, output.NO], name="confirm_publish",
        level=level, enter_confirms=enter, secure_value=secure,
    )
    confirm_github_overwrite = output.ConfirmPrompt(
        [output.YES, output.NO], name="confirm_github_overwrite",
        level=level, enter_confirms=enter, secure_value=secure,
    )
    confirm_persist_overwrite = output.ConfirmPrompt(
        [output.YES, output.NO, output.YES_ALL, output.NO_ALL],
        name="confirm_persist_overwrite", level="light",
    )
    confirm_gpg_key = output.ConfirmPrompt(
        [output.YES, output.NO], name="confirm_gpg_key", level="danger",
    )
