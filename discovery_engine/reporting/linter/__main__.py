"""CLI entry point: ``python -m discovery_engine.reporting.linter``."""
from __future__ import annotations

import sys

from discovery_engine.reporting.linter.engine import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
