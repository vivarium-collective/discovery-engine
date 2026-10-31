"""Configurable template for the "run this study" command the engine recommends.

The engine never executes this string; it only emits it as a recommendation
(executor tables, CLI hints). A consumer that drives a different runner
overrides the template or the runner binary:

    set_run_command_template("my-runner exec {slug} --ws {ws}")
    set_runner("my-runner")           # keep the shape, swap the binary
    reset_run_command()               # back to defaults

Placeholders: ``{runner}``, ``{slug}``, ``{ws}``. The defaults keep the
historical ``vivarium-workbench run study <slug> --workspace <ws>`` output.
"""
from __future__ import annotations

DEFAULT_RUN_COMMAND_TEMPLATE = "{runner} run study {slug} --workspace {ws}"
DEFAULT_RUNNER = "vivarium-workbench"

_template = DEFAULT_RUN_COMMAND_TEMPLATE
_runner = DEFAULT_RUNNER


def set_run_command_template(template: str) -> None:
    global _template
    _template = template or DEFAULT_RUN_COMMAND_TEMPLATE


def set_runner(runner: str) -> None:
    global _runner
    _runner = runner or DEFAULT_RUNNER


def reset_run_command() -> None:
    set_run_command_template(DEFAULT_RUN_COMMAND_TEMPLATE)
    set_runner(DEFAULT_RUNNER)


def run_command(slug: str, ws: str = "<ws>", *, template: str | None = None,
                runner: str | None = None) -> str:
    """Render the run command for ``slug`` (per-call args override the module config)."""
    return (template or _template).format(
        runner=runner or _runner, slug=slug, ws=ws)
