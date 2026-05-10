"""z3router.io – result extraction, visualization, and coordinate normalization."""

from .visualizer import extract_geometry, scale_geometry, plot_solution
from .normalize  import normalize_design, normalize_eda_pins, to_grid

__all__ = [
    # Geometry pipeline
    "extract_geometry",
    "scale_geometry",
    "plot_solution",
    # Normalization
    "normalize_design",
    "normalize_eda_pins",
    "to_grid",
]
rohithreddyappidi@Rohiths-MacBook-Air RouteSMT % cat z3router/tech/__init__.py
"""z3router.tech – technology layer definitions."""

from .layer_info import DEFAULT_TECH, Tech, ViaInfo, LayerVisualization

__all__ = ["DEFAULT_TECH", "Tech", "ViaInfo", "LayerVisualization"]
