"""Tests for discovery_engine.integration.server_preflight (served-version preflight)."""
from __future__ import annotations

import io
import json
import urllib.error
from contextlib import contextmanager

from discovery_engine.integration import server_preflight


@contextmanager
def _fake_response(payload: bytes):
    """Minimal urlopen()-compatible context manager returning `payload`."""
    yield io.BytesIO(payload)


def _patch_urlopen(monkeypatch, handler):
    monkeypatch.setattr(server_preflight.urllib.request, "urlopen", handler)


def test_matching_version_no_warning(monkeypatch):
    def handler(endpoint, timeout=None):
        return _fake_response(
            json.dumps({"version": "9.9.9", "git_rev": "abc1234"}).encode()
        )

    _patch_urlopen(monkeypatch, handler)
    warning = server_preflight.check_version(
        "http://127.0.0.1:8080", local_version="9.9.9"
    )
    assert warning is None


def test_skewed_version_emits_warning(monkeypatch):
    def handler(endpoint, timeout=None):
        return _fake_response(
            json.dumps({"version": "9.9.9", "git_rev": "deadbee"}).encode()
        )

    _patch_urlopen(monkeypatch, handler)
    warning = server_preflight.check_version(
        "http://127.0.0.1:8080", local_version="1.0.0"
    )
    assert warning is not None
    assert "9.9.9" in warning       # server version
    assert "deadbee" in warning     # server git_rev, for context
    assert "1.0.0" in warning       # local (skills') version
    assert warning.startswith("WARNING")


def test_endpoint_absent_404_no_warning_no_crash(monkeypatch):
    def handler(endpoint, timeout=None):
        raise urllib.error.HTTPError(
            endpoint, 404, "Not Found", hdrs=None, fp=None
        )

    _patch_urlopen(monkeypatch, handler)
    # Older server without /api/server-version → silently skip.
    warning = server_preflight.check_version(
        "http://127.0.0.1:8080", local_version="1.0.0"
    )
    assert warning is None


def test_unreachable_server_no_warning_no_crash(monkeypatch):
    def handler(endpoint, timeout=None):
        raise urllib.error.URLError("connection refused")

    _patch_urlopen(monkeypatch, handler)
    warning = server_preflight.check_version(
        "http://127.0.0.1:8080", local_version="1.0.0"
    )
    assert warning is None


def test_malformed_payload_no_warning(monkeypatch):
    def handler(endpoint, timeout=None):
        return _fake_response(b"not json")

    _patch_urlopen(monkeypatch, handler)
    warning = server_preflight.check_version(
        "http://127.0.0.1:8080", local_version="1.0.0"
    )
    assert warning is None


def test_read_server_url(tmp_path):
    info = tmp_path / ".pbg" / "server" / "server-info"
    info.parent.mkdir(parents=True)
    info.write_text(json.dumps({"url": "http://127.0.0.1:61341", "pid": 42}))
    assert server_preflight.read_server_url(tmp_path) == "http://127.0.0.1:61341"


def test_read_server_url_absent(tmp_path):
    assert server_preflight.read_server_url(tmp_path) is None


def test_preflight_prints_warning_to_stderr(monkeypatch, capsys):
    def handler(endpoint, timeout=None):
        return _fake_response(json.dumps({"version": "2.0.0"}).encode())

    _patch_urlopen(monkeypatch, handler)
    warning = server_preflight.preflight(
        url="http://127.0.0.1:8080",
    )
    # local package version differs from "2.0.0" → skew warning.
    assert warning is not None
    assert warning in capsys.readouterr().err


def test_cli_never_fails(monkeypatch):
    def handler(endpoint, timeout=None):
        raise urllib.error.URLError("nope")

    _patch_urlopen(monkeypatch, handler)
    assert server_preflight._main(["--url", "http://127.0.0.1:8080"]) == 0
