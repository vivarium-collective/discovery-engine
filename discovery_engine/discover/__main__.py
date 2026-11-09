"""Enable ``python -m discovery_engine.discover`` (the console script is
``discover``; this preserves the module-invocation form after the package split)."""
from __future__ import annotations

from discovery_engine.discover import main

raise SystemExit(main())
