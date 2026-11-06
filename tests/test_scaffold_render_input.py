r"""template-init.sh must receive the workspace name without a stray carriage return.

Regression: `_render` passed the name with `text=True`. On Windows, text mode
turns "\n" into "\r\n", so bash read the name as "lv-demo\r" and
template-init.sh rejected it as an invalid workspace name.
"""
from discovery_engine.integration import scaffold


def test_render_sends_name_as_bytes(tmp_path, monkeypatch):
    (tmp_path / "template-init.sh").write_text("")
    calls = []
    monkeypatch.setattr(
        scaffold.subprocess, "run", lambda *args, **kwargs: calls.append(kwargs)
    )

    scaffold._render(tmp_path, "lv-demo")

    assert calls[0]["input"] == b"lv-demo\n"
    assert "text" not in calls[0]