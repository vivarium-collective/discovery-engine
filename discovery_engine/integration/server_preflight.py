"""Served-version preflight for the /viva-* skills.

The /viva-* skills are HTTP clients of the vivarium-workbench server. They
resolve the server URL from ``<workspace_root>/.pbg/server/server-info`` and
then call ``/api/<endpoint>``. When the skills' expected API surface skews
from the *running* server (skills newer/older than the server), calls 404 and
look like bugs.

This module adds a lightweight, dependency-free preflight: after the URL is
resolved, ask the server ``GET /api/server-version`` and compare the reported
package version / git rev against *these* skills' own version
(``discovery_engine.__version__``). On skew we emit a clear WARNING to stderr
and let the call proceed — the mismatch is advisory, not fatal.

Older servers that predate ``/api/server-version`` (404 / connection error)
are handled silently: the endpoint is new, so its absence is expected and must
not spam warnings.

Stdlib only (``urllib``) — no ``requests`` dependency.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from discovery_engine import __version__ as LOCAL_VERSION

SERVER_INFO_REL = ".pbg/server/server-info"
SERVER_VERSION_ENDPOINT = "/api/server-version"
DEFAULT_TIMEOUT = 2.0


def read_server_url(root: str | Path = ".") -> str | None:
    """Return the workbench server URL from ``<root>/.pbg/server/server-info``.

    Returns None if the file is absent or unreadable (server not running).
    """
    info = Path(root) / SERVER_INFO_REL
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    url = data.get("url") if isinstance(data, dict) else None
    return url or None


def fetch_server_version(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> dict | None:
    """GET ``<url>/api/server-version`` and return its parsed JSON dict.

    Returns None (silently) on any failure the preflight must tolerate:
    the endpoint being absent on an older server (HTTP 404), the server not
    being reachable (connection error / timeout), or a non-JSON body. Only a
    successful, well-formed response yields a dict.
    """
    endpoint = url.rstrip("/") + SERVER_VERSION_ENDPOINT
    try:
        with urllib.request.urlopen(endpoint, timeout=timeout) as resp:
            body = resp.read()
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        # 404 (endpoint predates this feature) or unreachable → skip silently.
        return None
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def version_skew_warning(
    server: dict, *, local_version: str = LOCAL_VERSION
) -> str | None:
    """Return a warning string if the server's version skews from ours, else None.

    ``server`` is the ``/api/server-version`` payload
    (``{"git_rev": ..., "version": ...}``). Skew is decided on the package
    ``version`` field; ``git_rev`` is folded into the message for context.
    A server payload missing ``version`` is treated as no-skew (nothing to
    compare against) so malformed/partial responses don't spam warnings.
    """
    server_version = server.get("version")
    if not server_version:
        return None
    if str(server_version) == str(local_version):
        return None
    server_rev = server.get("git_rev")
    rev_suffix = f" ({server_rev})" if server_rev else ""
    return (
        f"WARNING: vivarium-workbench server is version {server_version}"
        f"{rev_suffix} but these /viva-* skills expect {local_version}; "
        "some API endpoints may 404 — update one side to match."
    )


def check_version(
    url: str,
    *,
    local_version: str = LOCAL_VERSION,
    timeout: float = DEFAULT_TIMEOUT,
) -> str | None:
    """Run the full preflight against ``url``; return a warning string or None.

    None means "no actionable skew" — matching versions, an older server
    without the endpoint, or an unreachable server. Never raises for the
    expected failure modes.
    """
    server = fetch_server_version(url, timeout=timeout)
    if server is None:
        return None
    return version_skew_warning(server, local_version=local_version)


def preflight(
    root: str | Path = ".",
    *,
    url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str | None:
    """Resolve the server (from ``url`` or ``root``'s server-info) and check it.

    Returns the warning string (also emitted to stderr) or None. Safe to call
    unconditionally from a skill's server-resolution path.
    """
    resolved = url or read_server_url(root)
    if not resolved:
        return None
    warning = check_version(resolved, timeout=timeout)
    if warning:
        print(warning, file=sys.stderr)
    return warning


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="discovery_engine.server_preflight",
        description="Warn (never fail) if the workbench server version skews "
        "from these skills.",
    )
    parser.add_argument(
        "--url",
        help="Server URL to probe directly; if omitted, read from "
        "<root>/.pbg/server/server-info.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Workspace root holding .pbg/server/server-info (default: cwd).",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)
    preflight(args.root, url=args.url, timeout=args.timeout)
    # Advisory only — always succeed so it never blocks a skill's real call.
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
