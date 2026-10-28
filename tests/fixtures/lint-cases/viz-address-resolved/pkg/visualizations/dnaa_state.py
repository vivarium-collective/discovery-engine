"""Test fixture: declared via explicit Visualization subclass."""

# We do NOT import the real Visualization base — the linter scans the file
# as text. A stub object is enough to keep the example self-contained.

class _Base:
    pass


class DnaAStateVisualization(_Base):
    """Lint resolves `local:DnaAStateVisualization` via the class declaration above."""
    pass
