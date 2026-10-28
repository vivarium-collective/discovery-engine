"""Filesystem helpers shared by the ``migrate_*`` modules."""
from __future__ import annotations

import subprocess
from pathlib import Path


def git_mv(src: Path, dest: Path, ws: Path) -> None:
    """``git -C ws mv src dest``, falling back to a plain move when the path
    isn't tracked (or ``ws`` isn't a git repo). Creates ``dest``'s parent."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["git", "-C", str(ws), "mv", str(src), str(dest)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:  # untracked / not a git repo -> plain move
        src.replace(dest)
