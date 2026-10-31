import pytest

from discovery_engine import run_command as rc
from discovery_engine.discovery_engine import _executor_for


@pytest.fixture(autouse=True)
def _reset():
    rc.reset_run_command()
    yield
    rc.reset_run_command()


def test_default_preserves_workbench_command():
    assert rc.run_command("s1") == "vivarium-workbench run study s1 --workspace <ws>"
    assert _executor_for("test", "s1")["command"] == \
        "vivarium-workbench run study s1 --workspace <ws>"


def test_override_template_changes_emitted_command():
    rc.set_run_command_template("my-runner exec {slug} --ws {ws}")
    assert _executor_for("test", "s1")["command"] == "my-runner exec s1 --ws <ws>"
    assert "my-runner exec s1" in _executor_for("branch", "s1")["command"]


def test_override_runner_keeps_shape():
    rc.set_runner("other-bin")
    assert _executor_for("test", "s1")["command"] == "other-bin run study s1 --workspace <ws>"
