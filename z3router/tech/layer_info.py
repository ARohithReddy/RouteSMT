"""
z3router.tech.layer_info
========================
Technology layer definitions for the Z3 router.

Defines the layer stack, routing directions, via connectivity,
manufacturing grid resolution, and visualization properties.

To support a different PDK, create a new ``Tech`` instance and pass it
into ``RouteSolver`` instead of using ``DEFAULT_TECH``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ViaInfo:
    """Connectivity record for a single via layer."""
    layer_above: str
    layer_below: str


@dataclass
class LayerVisualization:
    """Visualization properties used by the 3-D plotter."""
    color: str    # matplotlib-compatible hex or named color
    level: float  # z-axis height for 3-D plots


@dataclass
class Tech:
    """
    Complete technology description consumed by the router.

    Parameters
    ----------
    valid_routing_layers:
        Ordered list of legal routing layers (bottom → top).
    via_layers:
        Layer names that are vias (not wire layers).
    routing_directions:
        Maps each routing layer name to ``"horizontal"`` or ``"vertical"``.
    via_info:
        ``ViaInfo`` records keyed by via layer name.
    mfg_grid_res:
        Manufacturing grid resolution in microns.
    layer_vis:
        Optional visual properties per layer (used only by the plotter).
    """
    valid_routing_layers: List[str]
    via_layers: List[str]
    routing_directions: Dict[str, str]
    via_info: Dict[str, ViaInfo]
    mfg_grid_res: float = 0.0005
    layer_vis: Dict[str, LayerVisualization] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    def via_layers_above(self, layer: str) -> List[str]:
        """Return every via whose ``layer_below`` equals *layer*."""
        return [v for v, info in self.via_info.items() if info.layer_below == layer]

    def via_layers_below(self, layer: str) -> List[str]:
        """Return every via whose ``layer_above`` equals *layer*."""
        return [v for v, info in self.via_info.items() if info.layer_above == layer]

    def build_metal_adjacency(self) -> Dict[str, Dict[str, List[str]]]:
        """
        Build a per-metal-layer map of adjacent via layers.

        Returns
        -------
        dict
            ``{ layer_name: { "above": [via, ...], "below": [via, ...] } }``
        """
        adj: Dict[str, Dict[str, List[str]]] = {}
        for via, info in self.via_info.items():
            for lyr in (info.layer_above, info.layer_below):
                adj.setdefault(lyr, {"above": [], "below": []})
            adj[info.layer_below]["above"].append(via)
            adj[info.layer_above]["below"].append(via)
        for lyr in adj:
            adj[lyr]["above"] = list(set(adj[lyr]["above"]))
            adj[lyr]["below"] = list(set(adj[lyr]["below"]))
        return adj


# ---------------------------------------------------------------------------
# Default technology  (example 6T / 7.5T SRAM-style layer stack)
# ---------------------------------------------------------------------------

DEFAULT_TECH = Tech(
    valid_routing_layers=["tcn", "poly", "m0", "m1", "m2"],
    via_layers=["vt", "vg", "v0", "v1"],
    routing_directions={
        "tcn":  "vertical",
        "poly": "vertical",
        "m0":   "horizontal",
        "m1":   "vertical",
        "m2":   "horizontal",
    },
    via_info={
        "vt": ViaInfo(layer_above="m0",  layer_below="tcn"),
        "vg": ViaInfo(layer_above="m0",  layer_below="poly"),
        "v0": ViaInfo(layer_above="m1",  layer_below="m0"),
        "v1": ViaInfo(layer_above="m2",  layer_below="m1"),
    },
    mfg_grid_res=0.0005,
    layer_vis={
        "tcn":  LayerVisualization(color="#800000", level=0.05),
        "poly": LayerVisualization(color="#009933", level=0.06),
        "vt":   LayerVisualization(color="#ff6699", level=0.10),
        "vg":   LayerVisualization(color="#009999", level=0.11),
        "m0":   LayerVisualization(color="#cc0000", level=0.15),
        "v0":   LayerVisualization(color="#cc9900", level=0.20),
        "m1":   LayerVisualization(color="#003399", level=0.25),
        "v1":   LayerVisualization(color="#33ccff", level=0.30),
        "m2":   LayerVisualization(color="#996633", level=0.35),
    },
)
