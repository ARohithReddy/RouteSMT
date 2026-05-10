"""
design.py
=========
Design specification for the z3router.

This is the ONLY file you edit per cell / design.
All coordinates are in physical microns — normalization to integer
manufacturing-grid units is handled automatically by run.py.

Run with:
    python run.py --design design.py --mode visualize
    python run.py --design design.py --mode dump --output result.json
    python run.py --design design.py --mode eda
"""

# ---------------------------------------------------------------------------
# Technology
# Swap this for a custom Tech() instance if you have a different PDK.
# ---------------------------------------------------------------------------
from z3router.tech.layer_info import DEFAULT_TECH
TECH = DEFAULT_TECH

# ---------------------------------------------------------------------------
# Layer order
# All layers (metals + vias) available in this routing run, bottom to top.
# ---------------------------------------------------------------------------
LAYER_ORDER = ["poly", "vg", "tcn", "vt", "m0", "m1"]

# ---------------------------------------------------------------------------
# Net → allowed layers
# Each net is restricted to only the layers listed here.
# ---------------------------------------------------------------------------
NET_LAYER_MAP = {
    "netA": ["poly", "vg", "tcn", "vt", "m0", "m1"],
    "netB": ["poly", "vg", "tcn", "vt", "m0", "m1"],
}

# ---------------------------------------------------------------------------
# Routing tracks  { layer: [µm, ...] }
#
# Physical micron positions of each available routing track.
# Remove entries to block specific tracks (e.g. power straps).
# Vertical layers (poly, tcn, m1) define X-tracks.
# Horizontal layers (m0) define Y-tracks.
# ---------------------------------------------------------------------------
TRACK_INFO = {
    "poly": [0.000, 0.050, 0.100, 0.150, 0.200, 0.250, 0.300],
    "tcn":  [0.000, 0.050, 0.100, 0.150, 0.200, 0.250, 0.300],
    "m0":   [0.000, 0.050, 0.100, 0.150, 0.200, 0.250, 0.300],
    "m1":   [0.000, 0.050, 0.100, 0.150, 0.200, 0.250, 0.300],
}
# Block specific tracks (comment out or delete to re-enable)
TRACK_INFO["poly"].remove(0.150)   # blocked poly track
TRACK_INFO["poly"].remove(0.250)   # blocked poly track
TRACK_INFO["tcn"].remove(0.250)    # blocked tcn track
TRACK_INFO["m0"].remove(0.250)     # blocked m0 track
TRACK_INFO["m1"].remove(0.250)     # blocked m1 track

# ---------------------------------------------------------------------------
# Pin locations  { net: { layer: [[x_µm, y_µm], ...] } }
#
# Direct pin access-point coordinates in physical microns.
# Each [x, y] must land on a track defined in TRACK_INFO above.
# ---------------------------------------------------------------------------
PIN_INFO = {
    "netA": {"tcn":  [[0.050, 0.100], [0.150, 0.200]]},
    "netB": {"poly": [[0.200, 0.200], [0.100, 0.100]]},
}

# ---------------------------------------------------------------------------
# Solver options
#
#   use_hanan_grid       bool         Restrict routing to Hanan-grid tracks
#   pin_extension_layers list[str]    Layers used only for short pin stubs
#   max_trunks_per_net   dict[str,int] Max occupied tracks per layer per net
#   equal_wire_length    dict          {layer: [[net1,net2],...]} matched routing
# ---------------------------------------------------------------------------
OPTIONS = {
    "use_hanan_grid":       True,
    "pin_extension_layers": ["poly", "tcn"],
}

# ---------------------------------------------------------------------------
# EDA wire widths  (only used when --mode eda)
# Physical wire widths in microns, one entry per layer.
# ---------------------------------------------------------------------------
EDA_WIDTH_MAP = {
    "tcn":  0.016,
    "poly": 0.014,
    "vt":   0.008,
    "vg":   0.007,
    "m0":   0.020,
    "v0":   0.010,
    "m1":   0.030,
}
