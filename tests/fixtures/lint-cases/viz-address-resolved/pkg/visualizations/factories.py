"""Test fixture: classes declared via the @as_visualization decorator pattern.

The linter recognises both `update_<viz>` and the derived PascalCase / snake
class names.
"""

def update_time_series_plot(state):
    """Resolves `local:TimeSeriesPlot` (PascalCase) and `local:time_series_plot` (snake)."""
    return {"html": ""}


def update_heatmap_view(state):
    """Resolves `local:HeatmapView` and `local:heatmap_view`."""
    return {"html": ""}
