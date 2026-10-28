"""A minimal Process-like class for fixture purposes (no real process-bigraph dep needed)."""


class FakeProcess:
    """Stub. Real-world wrappers inherit from process_bigraph.Process."""
    config_schema = {"k": {"_type": "float", "_default": 1.0}}

    def __init__(self, config=None, core=None):
        self.config = {**self.config_schema_defaults(), **(config or {})}
        self.core = core

    @classmethod
    def config_schema_defaults(cls):
        return {k: v.get("_default") for k, v in cls.config_schema.items()}

    def inputs(self):
        return {"x": "float"}

    def outputs(self):
        return {"y": "float"}

    def update(self, state, interval=1.0):
        return {"y": state["x"] * self.config["k"]}
