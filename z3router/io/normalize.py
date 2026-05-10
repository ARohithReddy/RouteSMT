"""
z3router.io.normalize
=====================
Converts physical (floating-point micron) coordinates to integer
manufacturing-grid units consumed by the router.

Two entry points are provided for the two distinct input formats:

``normalize_design``
    For hand-written ``design.py`` files where pins are specified as
    **direct [x, y] point coordinates** in microns.  This is the normal
    path when running ``run.py``.

``normalize_eda_pins``
    For EDA-tool-driven flows (e.g. ``routeInt12.py``) where pin
    locations are **bounding boxes** extracted from cell shapes.  The
    function finds all routing-track intersections that fall inside each
    box and returns those as the valid pin access points.

Both functions return the same output types so the rest of the router
does not need to know which path was used.

Round-trip guarantee
--------------------
Coordinates that are exact multiples of ``mfg_grid_res`` round-trip
losslessly:  ``to_grid(x, res) * res == x``.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from z3router.tech.layer_info import Tech

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

# Physical (micron) inputs
PhysTrackInfo = Dict[str, List[float]]                          # layer -> [µm, ...]
PhysPinPoints = Dict[str, Dict[str, List[List[float]]]]         # net -> layer -> [[x,y],...]
PhysPinBoxes  = Dict[str, Dict[str, List[                       # net -> layer -> [bbox,...]
    Tuple[Tuple[float, float], Tuple[float, float]]
]]]

# Integer-grid outputs  (same shape regardless of input path)
IntTrackInfo = Dict[str, List[int]]                             # layer -> [grid_idx,...]
IntPinInfo   = Dict[str, Dict[str, List[List[int]]]]            # net -> layer -> [[x,y],...]


# ---------------------------------------------------------------------------
# Shared primitive
# ---------------------------------------------------------------------------

def to_grid(value: float, grid_resolution: float) -> int:
    """Round a physical micron coordinate to the nearest grid index."""
    return round(value / grid_resolution)


# ---------------------------------------------------------------------------
# Path 1 — design.py flow  (direct [x, y] pin points)
# ---------------------------------------------------------------------------

def normalize_design(
    track_info: PhysTrackInfo,
    pin_info:   PhysPinPoints,
    tech:       Tech,
) -> Tuple[IntTrackInfo, IntPinInfo]:
    """
    Normalize a ``design.py``-style spec from physical microns to grid indices.

    Pins are given as explicit ``[x, y]`` points (not bounding boxes).
    Each point is snapped to the nearest manufacturing-grid index.
    Track lists are snapped the same way.

    Parameters
    ----------
    track_info:
        ``{ layer: [µm, ...] }``
        One list of track positions per routing layer.
    pin_info:
        ``{ net: { layer: [[x_µm, y_µm], ...] } }``
        Direct pin access points in physical microns.
    tech:
        Technology definition (provides ``mfg_grid_res``).

    Returns
    -------
    (int_track_info, int_pin_info)
        Both dicts use integer manufacturing-grid indices.

    Examples
    --------
    >>> from z3router.tech.layer_info import DEFAULT_TECH
    >>> track_info = {"poly": [0.025, 0.075, 0.125],
    ...               "m0":   [0.036, 0.072, 0.108]}
    >>> pin_info   = {"netA": {"poly": [[0.025, 0.036], [0.125, 0.108]]}}
    >>> t, p = normalize_design(track_info, pin_info, DEFAULT_TECH)
    """
    res = tech.mfg_grid_res

    int_track_info: IntTrackInfo = {
        layer: [to_grid(t, res) for t in tracks]
        for layer, tracks in track_info.items()
    }

    int_pin_info: IntPinInfo = {}
    for net, net_pins in pin_info.items():
        int_pin_info[net] = {}
        for layer, xy_list in net_pins.items():
            int_pin_info[net][layer] = [
                [to_grid(x, res), to_grid(y, res)]
                for x, y in xy_list
            ]

    return int_track_info, int_pin_info


# ---------------------------------------------------------------------------
# Path 2 — EDA tool flow  (bounding-box pin shapes from layout)
# ---------------------------------------------------------------------------

def normalize_eda_pins(
    track_info: PhysTrackInfo,
    pin_boxes:  PhysPinBoxes,
    tech:       Tech,
) -> Tuple[IntTrackInfo, IntPinInfo]:
    """
    Normalize an EDA-tool-driven spec from physical microns to grid indices.

    Pin locations are **bounding boxes** extracted from cell shapes (as
    returned by ``cc.db.transform(shape.bBox, ...)`` in Synopsys CC).
    For each box, every routing-track intersection that falls *strictly
    inside* the box is collected as a valid pin access point.

    Parameters
    ----------
    track_info:
        ``{ layer: [µm, ...] }``
    pin_boxes:
        ``{ net: { layer: [ ((x1,y1), (x2,y2)), ... ] } }``
        Bounding boxes; corner ordering is not required to be canonical.
    tech:
        Technology definition.

    Returns
    -------
    (int_track_info, int_pin_info)
        Both dicts use integer manufacturing-grid indices.
    """
    res = tech.mfg_grid_res

    # Collect x- and y-track sets from layer routing directions
    x_tracks_phys: set[float] = set()
    y_tracks_phys: set[float] = set()

    for layer, tracks in track_info.items():
        if layer in tech.via_layers:
            continue
        direction = tech.routing_directions.get(layer)
        if direction == "horizontal":
            y_tracks_phys.update(tracks)
        elif direction == "vertical":
            x_tracks_phys.update(tracks)

    x_tracks = sorted(x_tracks_phys)
    y_tracks = sorted(y_tracks_phys)

    # Find track intersections inside each bounding box
    int_pin_info: IntPinInfo = {}
    for net, net_pins in pin_boxes.items():
        int_pin_info[net] = {}
        for layer, bbox_list in net_pins.items():
            int_pin_info[net][layer] = []
            for bbox in bbox_list:
                (x1, y1), (x2, y2) = bbox
                x_lo, x_hi = min(x1, x2), max(x1, x2)
                y_lo, y_hi = min(y1, y2), max(y1, y2)
                for x in x_tracks:
                    for y in y_tracks:
                        if x_lo < x < x_hi and y_lo < y < y_hi:
                            pt = [to_grid(x, res), to_grid(y, res)]
                            if pt not in int_pin_info[net][layer]:
                                int_pin_info[net][layer].append(pt)

    int_track_info: IntTrackInfo = {
        layer: [to_grid(t, res) for t in tracks]
        for layer, tracks in track_info.items()
    }

    return int_track_info, int_pin_info
