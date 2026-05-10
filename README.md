# z3router

**z3router** is a Z3-based SMT solver for multi-net VLSI routing.

It formulates the routing problem as a satisfiability / optimization problem:
Boolean node variables are placed on a per-layer routing grid, integer flow
variables prove that each net forms a single connected Steiner tree, and the
Z3 Optimizer minimizes total wire usage.

---

## Features

- Multi-net, multi-layer routing with via support
- Flow-based connectivity proof (no disconnected wire fragments)
- Hanan-grid pruning to reduce the search space
- Pin-extension-only layer mode (e.g. local poly / tcn stubs)
- Maximum trunk count per layer
- Equal wire-length matching across nets
- **Physical micron coordinates** in design files — normalization is automatic
- Clean separation between solver core, technology, I/O, and EDA integration

---

## Installation

```bash
pip install z3-solver matplotlib   # runtime dependencies
pip install -e ".[dev]"            # editable install with dev tools
```

Requires **Python ≥ 3.9**.

---

## Coordinate flow

```
design.py          run.py                          run.py
(physical µm)  →  normalize_design()  →  solver  →  scale_geometry()  →  output (µm)
```

`design.py` always uses **physical micron coordinates**.  
`run.py` normalizes them to integer manufacturing-grid units before solving, then
scales the result back to microns for all three output modes.

---

## Quick start

The workflow is split into two files:

| File | Role |
|---|---|
| `design.py` | **You edit this.** One file per cell / design. All coordinates in µm. |
| `run.py` | **Never changes.** Loads any `design.py`, normalizes, solves, outputs. |

**1. Describe your design in `design.py`:**

```python
from z3router.tech.layer_info import DEFAULT_TECH
TECH = DEFAULT_TECH

LAYER_ORDER   = ["poly", "vg", "tcn", "vt", "m0", "m1"]

NET_LAYER_MAP = {
    "netA": ["poly", "vg", "tcn", "vt", "m0", "m1"],
    "netB": ["poly", "vg", "tcn", "vt", "m0", "m1"],
}

# Physical micron track positions
TRACK_INFO = {
    "poly": [0.000, 0.050, 0.100, 0.200, 0.300],   # µm
    "tcn":  [0.000, 0.050, 0.100, 0.150, 0.300],
    "m0":   [0.000, 0.050, 0.100, 0.150, 0.200],
    "m1":   [0.000, 0.050, 0.100, 0.150, 0.300],
}

# Direct [x, y] pin coordinates in microns
PIN_INFO = {
    "netA": {"tcn":  [[0.050, 0.100], [0.150, 0.200]]},
    "netB": {"poly": [[0.200, 0.200], [0.100, 0.100]]},
}

OPTIONS = {
    "use_hanan_grid":       True,
    "pin_extension_layers": ["poly", "tcn"],
}

EDA_WIDTH_MAP = {"poly": 0.014, "tcn": 0.016, "m0": 0.020, "m1": 0.030}
```

**2. Run with your chosen output mode:**

```bash
# 3-D matplotlib visualization (axes in µm)
python run.py --design design.py --mode visualize

# JSON dump (all coordinates in µm)
python run.py --design design.py --mode dump --output result.json

# Write shapes to Synopsys Custom Compiler (must run inside CC session)
python run.py --design design.py --mode eda
```

Different cells → different design files, same `run.py`:

```bash
python run.py --design cells/inv_x1.py  --mode visualize
python run.py --design cells/nand2_x2.py --mode dump --output out/nand2.json
```

---

## Project layout

```
z3router/
├── design.py                # ← YOU EDIT THIS  (physical µm, one per cell)
├── run.py                   # ← always the same; normalizes + runs
│
├── z3router/                # library package
│   ├── __init__.py
│   ├── tech/
│   │   └── layer_info.py    # Tech dataclass, ViaInfo, DEFAULT_TECH
│   ├── core/
│   │   ├── grid.py          # routing grid builder  (integer grid units)
│   │   ├── variables.py     # Z3 node / edge / flow variable factories
│   │   └── solver.py        # RouteSolver — top-level entry point
│   ├── constraints/
│   │   └── routing.py       # all Z3 constraint generators (pure functions)
│   ├── io/
│   │   ├── normalize.py     # µm → grid  (normalize_design / normalize_eda_pins)
│   │   ├── visualizer.py    # extract_geometry · scale_geometry · plot_solution
│   │   └── __init__.py
│   └── eda/
│       └── synopsys_cc.py   # Synopsys CC shape writer (accepts µm geometry)
│
├── examples/
│   └── two_net_basic.py     # standalone example showing the full pipeline
├── tests/
│   ├── test_tech.py
│   ├── test_grid.py
│   └── test_normalize.py
├── pyproject.toml
└── README.md
```

---

## Normalization details

`normalize_design` is used by `run.py` and the standalone example:

```python
from z3router.io.normalize import normalize_design

int_track_info, int_pin_info = normalize_design(TRACK_INFO, PIN_INFO, tech)
# int_track_info["poly"] = [0, 100, 200, 400, 600]  (units of mfg_grid_res = 0.0005 µm)
# int_pin_info["netA"]["tcn"] = [[100, 200], [300, 400]]
```

For EDA-tool flows where pin locations are bounding boxes (from `cc.db.transform`):

```python
from z3router.io.normalize import normalize_eda_pins

int_track_info, int_pin_info = normalize_eda_pins(track_info, pin_boxes, tech)
```

---

## Dump JSON format

All coordinates are physical microns.  Via layers emit `"points"` only;
metal layers emit `"segments"` only:

```json
{
  "netA": {
    "vg":   { "points":   [[0.005, 0.010]] },
    "poly": { "segments": [[[0.005, 0.010], [0.015, 0.010]]] },
    "m0":   { "segments": [[[0.005, 0.010], [0.005, 0.020]]] }
  }
}
```

---

## Defining a custom technology

```python
from z3router.tech.layer_info import Tech, ViaInfo, LayerVisualization

my_tech = Tech(
    valid_routing_layers = ["m1", "m2", "m3"],
    via_layers           = ["v1", "v2"],
    routing_directions   = {"m1": "horizontal", "m2": "vertical", "m3": "horizontal"},
    via_info             = {
        "v1": ViaInfo(layer_above="m2", layer_below="m1"),
        "v2": ViaInfo(layer_above="m3", layer_below="m2"),
    },
    mfg_grid_res = 0.001,
    layer_vis    = {
        "m1": LayerVisualization(color="#cc0000", level=0.10),
        "v1": LayerVisualization(color="#ff9900", level=0.15),
        "m2": LayerVisualization(color="#0033cc", level=0.20),
    },
)
```

---

## Solver options

| Key | Type | Description |
|---|---|---|
| `use_hanan_grid` | `bool` | Restrict routing to the closest tracks near each pin |
| `pin_extension_layers` | `list[str]` | Layers used only for short pin stubs |
| `max_trunks_per_net` | `dict[str, int]` | Maximum occupied tracks per layer |
| `equal_wire_length` | `dict` | `{layer: [[net1, net2], ...]}` — matched routing |

---

## EDA integration (Synopsys Custom Compiler)

```python
from z3router.eda.synopsys_cc import write_shapes_from_geometry
import snps.custom.cmd as cc

# geometry is already in physical µm (output of scale_geometry)
write_shapes_from_geometry(
    cell_view = cc.object.fromOa(tcl.eval('ed')),
    geometry  = geometry,
    tech      = my_tech,
    width_map = {"m1": 0.020, "v1": 0.010, "m2": 0.030},
)
```

---

## Running tests

```bash
pytest
```

---

## License

MIT
