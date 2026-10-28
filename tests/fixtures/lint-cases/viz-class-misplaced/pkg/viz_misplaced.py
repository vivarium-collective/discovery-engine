"""A Visualization subclass defined at the package ROOT (viz_misplaced.py) —
the wrong place. The dashboard's local: resolver and the linter only discover
classes under pkg/visualizations/, so `local:MisplacedViz` won't resolve even
though the class plainly exists.
"""
from viva_superpowers.visualization import Visualization


class MisplacedViz(Visualization):
    def inputs(self):
        return {"x": "float"}

    def render(self):
        return "<p>misplaced</p>"
