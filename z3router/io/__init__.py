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
