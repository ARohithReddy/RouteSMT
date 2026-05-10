"""
z3router.core.grid
==================
Builds the per-layer routing grid from track definitions and pin locations.

The grid is the foundation on which Z3 Boolean node variables are placed.
Every routing layer gets a filtered (x-list, y-list) pair; via layers get
the intersection of their two adjacent metal-layer coordinate lists.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from z3router.tech.layer_info import Tech

# Coord = integer grid index (already on manufacturing grid)
LayerGrid = Dict[str, Tuple[List[int], List[int]]]  # layer -> (xs, ys)
PinDict   = Dict[str, Dict[str, List[List[int]]]]    # net -> layer -> [[x,y],...]
TrackDict = Dict[str, List[int]]                     # layer -> [track coords]


def build_global_xy(
    track_info: TrackDict,
    pin_info: PinDict,
    tech: Tech,
) -> Tuple[List[int], List[int]]:
    """
    Collect the union of all x and y coordinates across every layer and pin.

    Horizontal layers contribute their tracks to the *y* set;
    vertical layers contribute to the *x* set.
    Pin locations are always added to both sets so every pin sits on-grid.

    Parameters
    ----------
    track_info:
        ``{ layer: [track_coord, ...] }`` — integer manufacturing-grid units.
    pin_info:
        ``{ net: { layer: [[x, y], ...] } }``
    tech:
        Technology definition.

    Returns
    -------
    (sorted_x_list, sorted_y_list)
    """
    x_set: set[int] = set()
    y_set: set[int] = set()

    for layer, tracks in track_info.items():
        if layer in tech.via_layers:
            continue
        direction = tech.routing_directions.get(layer)
        if direction == "horizontal":
            y_set.update(tracks)
        elif direction == "vertical":
            x_set.update(tracks)

    for net_pins in pin_info.values():
        for xy_list in net_pins.values():
            for x, y in xy_list:
                x_set.add(x)
                y_set.add(y)

    return sorted(x_set), sorted(y_set)


def build_layer_grids(
    layer_order: List[str],
    track_info: TrackDict,
    global_xy: Tuple[List[int], List[int]],
    tech: Tech,
) -> LayerGrid:
    """
    Compute the ``(x_list, y_list)`` grid for every layer in *layer_order*.

    Metal layers are filtered so only the track coordinates relevant to
    that layer's routing direction are kept.  Via layers receive the
    intersection of their two adjacent metal-layer coordinate sets.

    Parameters
    ----------
    layer_order:
        Ordered list of all layers (metals + vias) used in this run.
    track_info:
        Per-layer track coordinate lists.
    global_xy:
        Output of :func:`build_global_xy`.
    tech:
        Technology definition.

    Returns
    -------
    LayerGrid
        ``{ layer: (sorted_xs, sorted_ys) }``
    """
    global_x, global_y = global_xy
    grids: LayerGrid = {}

    # --- metal layers first ---
    for layer in layer_order:
        if layer in tech.via_layers:
            continue
        direction = tech.routing_directions.get(layer)
        layer_tracks = set(track_info.get(layer, []))
        if direction == "horizontal":
            grids[layer] = (list(global_x), sorted(y for y in global_y if y in layer_tracks))
        elif direction == "vertical":
            grids[layer] = (sorted(x for x in global_x if x in layer_tracks), list(global_y))

    # --- via layers: intersection of adjacent metals ---
    for layer in layer_order:
        if layer not in tech.via_layers:
            continue
        via = tech.via_info[layer]
        layer_above = via.layer_above
        layer_below = via.layer_below
        xs = sorted(set(grids[layer_above][0]) & set(grids[layer_below][0]))
        ys = sorted(set(grids[layer_above][1]) & set(grids[layer_below][1]))
        grids[layer] = (xs, ys)

    return grids


def check_pins_on_grid(
    pin_info: PinDict,
    layer_grids: LayerGrid,
    tech: Tech,
) -> bool:
    """
    Verify that every pin coordinate falls on the routing grid.

    Logs offending pins and returns ``False`` on the first violation.
    Returns ``True`` when all pins are on-grid.
    """
    all_ok = True
    for net, net_pins in pin_info.items():
        for layer, xy_list in net_pins.items():
            direction = tech.routing_directions.get(layer)
            if direction == "horizontal":
                off_grid = [pt for pt in xy_list if pt[1] not in layer_grids[layer][1]]
            elif direction == "vertical":
                off_grid = [pt for pt in xy_list if pt[0] not in layer_grids[layer][0]]
            else:
                continue
            if off_grid:
                print(f"[z3router] Pin not on grid — net={net!r}, layer={layer!r}, points={off_grid}")
                all_ok = False
    return all_ok
