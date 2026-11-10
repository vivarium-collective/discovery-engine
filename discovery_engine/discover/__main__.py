"""CLI entry point: ``python -m discovery_engine.discover``.

The console script is ``discover`` (``discovery_engine.discover:main``); this
preserves the module-invocation form after the package split. Guarded so that
importing this module (e.g. an import-surface test) does not execute the CLI.
"""
from __future__ import annotations

import sys

from discovery_engine.discover import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
