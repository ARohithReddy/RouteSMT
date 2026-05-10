"""
z3router.io.visualizer
=======================
Converts a raw route solution into geometric segments/points and renders
a 3-D matplotlib figure — one figure per net.

Geometry extraction
-------------------
The solution dict contains a Boolean per grid node.  This module:

1. Groups consecutive ``True`` nodes into wire segments (runs along the
   routing direction of each metal layer).
2. Promotes isolated ``True`` nodes to point markers (e.g. stubs or
   single-track vias).
3. Renders vias as vertical lines between the z-levels of the two
   adjacent metal layers.

Usage::

    from z3router.io.visualizer import extract_geometry, plot_solution

    geometry = extract_geometry(solution, pin_info, tech)
    plot_solution(geometry, tech)
"""

from __future__ import annotations

from typing import Dict, List, Tuple, Optional

import matplotlib.pyplot as plt

from z3router.tech.layer_info import Tech

# Type aliases
RouteSolution = Dict[str, Dict[str, Dict[int, Dict[int, bool]]]]
PinInfo       = Dict[str, Dict[str, List[List[int]]]]

Point   = List[int]          # [x, y]
Segment = Tuple[Point, Point]  # ([x0,y0], [x1,y1])

LayerGeometry = Dict[str, Dict[str, object]]  # layer -> {"points": [...], "segments": [...]}
NetGeometry   = Dict[str, LayerGeometry]       # net   -> LayerGeometry


def extract_geometry(
    solution: RouteSolution,
    pin_info: PinInfo,
    tech: Tech,
) -> NetGeometry:
    """
    Convert a Boolean solution dict into lists of segments and points.

    Consecutive active nodes along the routing direction are merged into
    a single segment; isolated nodes become point markers.

    Parameters
    ----------
    solution:
        Output of :meth:`RouteSolver.solve`.
    pin_info:
        Pin locations (added as point markers to their respective layers).
    tech:
        Technology definition (needed for routing directions and via info).

    Returns
    -------
    NetGeometry
        ``{ net: { layer: { "points": [...], "segments": [...] } } }``
    """
    result: NetGeometry = {}

    for net, net_sol in solution.items():
        result[net] = {}
        for layer, x_dict in net_sol.items():
            points:   List[Point]   = []
            segments: List[Segment] = []

            direction = tech.routing_directions.get(layer)
            is_via = layer in tech.via_layers

            # Iterate in row-major order; track previous indices for run detection
            x_keys = sorted(x_dict.keys())
            for xi, x in enumerate(x_keys):
                x_prev = x_keys[xi - 1] if xi > 0 else None
                y_keys = sorted(x_dict[x].keys())
                for yi, y in enumerate(y_keys):
                    y_prev = y_keys[yi - 1] if yi > 0 else None
                    if not x_dict[x][y]:
                        continue

                    if not is_via:
                        if direction == "vertical" and y_prev is not None and x_dict[x].get(y_prev):
                            segments.append(([x, y_prev], [x, y]))
                            _discard(points, [x, y_prev])
                            _discard(points, [x, y])
                        elif direction == "horizontal" and x_prev is not None and x_dict.get(x_prev, {}).get(y):
                            segments.append(([x_prev, y], [x, y]))
                            _discard(points, [x_prev, y])
                            _discard(points, [x, y])
                        else:
                            if [x, y] not in points:
                                points.append([x, y])
                    else:
                        # via — always a point (rendered as vertical connector)
                        if [x, y] not in points:
                            points.append([x, y])

            result[net][layer] = {"points": points, "segments": segments}

        # Merge pin markers into their layers
        for layer, xy_list in pin_info.get(net, {}).items():
            if layer not in result[net]:
                result[net][layer] = {"points": [], "segments": []}
            for xy in xy_list:
                if xy not in result[net][layer]["points"]:
                    result[net][layer]["points"].append(xy)

    return result


def scale_geometry(geometry: NetGeometry, scale: float) -> NetGeometry:
    """
    Convert all coordinates in *geometry* from grid units to physical microns.

    Multiply every x and y value by *scale* (``tech.mfg_grid_res``).
    Returns a new ``NetGeometry`` dict; the input is not modified.

    Parameters
    ----------
    geometry:
        Output of :func:`extract_geometry` (integer grid coordinates).
    scale:
        Scale factor — typically ``tech.mfg_grid_res`` (microns per grid unit).

    Returns
    -------
    NetGeometry
        Same structure, all coordinates converted to physical microns (floats).
    """
    def _scale_pt(pt: List) -> List[float]:
        return [pt[0] * scale, pt[1] * scale]

    def _scale_seg(seg) -> List[List[float]]:
        return [_scale_pt(seg[0]), _scale_pt(seg[1])]

    scaled: NetGeometry = {}
    for net, layer_geo in geometry.items():
        scaled[net] = {}
        for layer, geo in layer_geo.items():
            scaled[net][layer] = {
                "points":   [_scale_pt(pt) for pt in geo["points"]],
                "segments": [_scale_seg(seg) for seg in geo["segments"]],
            }
    return scaled


def plot_solution(geometry: NetGeometry, tech: Optional[Tech] = None) -> None:
    """
    Render the routing solution as a 3-D matplotlib figure.

    Expects *geometry* to already contain **physical micron coordinates**
    (i.e. the output of :func:`scale_geometry`).  Call :func:`extract_geometry`
    and :func:`scale_geometry` before this function.

    One figure is created per net.  Wire segments are drawn as lines,
    point markers as squares, and vias as vertical connectors between
    the z-levels of adjacent metal layers.

    Parameters
    ----------
    geometry:
        Scaled output of :func:`scale_geometry`.
    tech:
        Technology definition (provides layer z-levels and colors).
        If omitted, layers are drawn in a single default color.
    """
    for net, layer_geo in geometry.items():
        fig = plt.figure(figsize=(8, 6))
        ax = fig.add_subplot(111, projection="3d")
        ax.set_title(f"Net: {net}", fontsize=12)

        for layer, geo in layer_geo.items():
            vis = tech.layer_vis.get(layer) if tech else None
            color   = vis.color if vis else "#888888"
            z_level = vis.level if vis else 0.0

            # Wire segments
            for seg in geo["segments"]:
                ax.plot3D(
                    [seg[0][0], seg[1][0]],
                    [seg[0][1], seg[1][1]],
                    [z_level,   z_level],
                    color=color,
                    linewidth=2,
                )

            is_via = tech and layer in tech.via_layers

            # Point markers (stubs, isolated pins)
            if not is_via:
                for pt in geo["points"]:
                    ax.scatter([pt[0]], [pt[1]], [z_level],
                               marker="s", color=color, s=40)

            # Via connectors (vertical lines between metal z-levels)
            if is_via:
                via = tech.via_info[layer]
                z_below = tech.layer_vis[via.layer_below].level
                z_above = tech.layer_vis[via.layer_above].level
                for pt in geo["points"]:
                    ax.plot3D([pt[0], pt[0]], [pt[1], pt[1]],
                              [z_below, z_above],
                              color=color, linewidth=2, linestyle="--")

        ax.set_xlabel("X (µm)")
        ax.set_ylabel("Y (µm)")
        ax.set_zlabel("Layer")
        plt.tight_layout()
        plt.show()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _discard(lst: List, item) -> None:
    """Remove *item* from *lst* if present (no error if absent)."""
    try:
        lst.remove(item)
    except ValueError:
        pass
