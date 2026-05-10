"""
run.py
======
z3router runner — this file never changes between designs.

Workflow
--------
1. Load the design file (any *.py matching the design spec).
2. Normalize physical micron coordinates → integer manufacturing-grid units.
3. Run the Z3 solver.
4. Scale the solution back to physical microns.
5. Output via the chosen mode.

Usage
-----
Visualize (3-D matplotlib plot):
    python run.py --design design.py --mode visualize

Dump solution to JSON (physical micron coordinates):
    python run.py --design design.py --mode dump --output result.json

Write shapes to EDA tool (Synopsys Custom Compiler):
    python run.py --design design.py --mode eda

Arguments
---------
--design  PATH   Path to the design spec file          [default: design.py]
--mode    MODE   visualize | dump | eda                [default: visualize]
--output  PATH   Output file for dump mode             [default: result.json]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


# ---------------------------------------------------------------------------
# Design loader
# ---------------------------------------------------------------------------

def _load_design(path: str) -> ModuleType:
    """Dynamically import any design spec file by path."""
    spec = importlib.util.spec_from_file_location("_design", path)
    if spec is None or spec.loader is None:
        print(f"[run] Cannot load design file: {path}")
        sys.exit(1)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)   # type: ignore[union-attr]
    return module


def _require(design: ModuleType, attr: str):
    """Return a required design attribute; exit with a clear message if absent."""
    val = getattr(design, attr, None)
    if val is None:
        print(f"[run] design file is missing required attribute: {attr!r}")
        sys.exit(1)
    return val


# ---------------------------------------------------------------------------
# Output modes  (all receive geometry in physical microns)
# ---------------------------------------------------------------------------

def _mode_visualize(geometry, tech) -> None:
    from z3router.io.visualizer import plot_solution
    plot_solution(geometry, tech)


def _mode_dump(geometry, tech, output_path: str) -> None:
    """
    Write the scaled geometry to a JSON file.

    All coordinates are physical microns (floats).

    Output rules:
    - Via layers    → ``"points"`` only   (each via is a single location)
    - Metal layers  → ``"segments"`` only (wires are fully merged into runs)

    Structure::

        {
          "netA": {
            "vg":   { "points":   [[x, y], ...] },
            "poly": { "segments": [[[x0,y0],[x1,y1]], ...] }
          },
          ...
        }
    """
    filtered = {}
    for net, layer_geo in geometry.items():
        filtered[net] = {}
        for layer, geo in layer_geo.items():
            if layer in tech.via_layers:
                filtered[net][layer] = {"points": geo["points"]}
            else:
                filtered[net][layer] = {"segments": geo["segments"]}

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(filtered, indent=2))
    print(f"[run] Solution written to {out.resolve()}")


def _mode_eda(geometry, tech, width_map) -> None:
    """Write shapes into the currently open Synopsys Custom Compiler cell."""
    try:
        import snps.custom.cmd as cc   # type: ignore
    except ImportError:
        print("[run] Synopsys Custom Compiler is not available in this environment.")
        print("      Run inside a Custom Compiler Python session.")
        sys.exit(1)

    from z3router.eda.synopsys_cc import write_shapes_from_geometry

    try:
        cell_view = cc.object.fromOa(tcl.eval("ed"))   # type: ignore[name-defined]
    except Exception as exc:
        print(f"[run] Could not obtain cell view from EDA tool: {exc}")
        sys.exit(1)

    write_shapes_from_geometry(
        cell_view = cell_view,
        geometry  = geometry,
        tech      = tech,
        width_map = width_map,
    )
    print("[run] Shapes written to cell view.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog        = "run.py",
        description = "z3router — SMT-based VLSI multi-net router",
        formatter_class = argparse.RawDescriptionHelpFormatter,
        epilog = (
            "Examples:\n"
            "  python run.py --design design.py --mode visualize\n"
            "  python run.py --design design.py --mode dump --output out/result.json\n"
            "  python run.py --design design.py --mode eda\n"
            "\n"
            "  # Different cell, same runner:\n"
            "  python run.py --design cells/nand2_x2.py --mode visualize\n"
        ),
    )
    parser.add_argument(
        "--design", "-d",
        default = "design.py",
        metavar = "PATH",
        help    = "Path to the design spec file (default: design.py)",
    )
    parser.add_argument(
        "--mode", "-m",
        choices = ["visualize", "dump", "eda"],
        default = "visualize",
        help    = "Output mode: visualize | dump | eda  (default: visualize)",
    )
    parser.add_argument(
        "--output", "-o",
        default = "result.json",
        metavar = "PATH",
        help    = "Output JSON file for dump mode (default: result.json)",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()

    # ------------------------------------------------------------------
    # 1. Load design file
    # ------------------------------------------------------------------
    design_path = Path(args.design)
    if not design_path.exists():
        print(f"[run] Design file not found: {design_path}")
        sys.exit(1)

    print(f"[run] Loading design : {design_path.resolve()}")
    design = _load_design(str(design_path))

    tech          = getattr(design, "TECH",          None)
    layer_order   = _require(design, "LAYER_ORDER")
    net_layer_map = _require(design, "NET_LAYER_MAP")
    track_info    = _require(design, "TRACK_INFO")     # physical microns
    pin_info      = _require(design, "PIN_INFO")       # physical microns
    options       = getattr(design, "OPTIONS",        {})
    eda_width_map = getattr(design, "EDA_WIDTH_MAP",  {})

    if tech is None:
        from z3router.tech.layer_info import DEFAULT_TECH
        tech = DEFAULT_TECH
        print("[run] TECH not set in design — using DEFAULT_TECH")

    # ------------------------------------------------------------------
    # 2. Normalize: physical microns → integer manufacturing-grid units
    #
    #    design.py always specifies coordinates in µm.
    #    The solver works entirely in integer grid indices.
    #    scale_geometry() converts back to µm at the end.
    # ------------------------------------------------------------------
    from z3router.io.normalize import normalize_design

    int_track_info, int_pin_info = normalize_design(track_info, pin_info, tech)

    print(f"[run] Grid resolution : {tech.mfg_grid_res} µm/grid-unit")
    print(f"[run] Nets            : {list(net_layer_map.keys())}")
    print(f"[run] Layers in use   : {layer_order}")

    # ------------------------------------------------------------------
    # 3. Run solver  (operates on integer grid coordinates)
    # ------------------------------------------------------------------
    from z3router import RouteSolver

    solver = RouteSolver(
        layer_order   = layer_order,
        net_layer_map = net_layer_map,
        track_info    = int_track_info,
        tech          = tech,
        options       = options,
    )

    pin_ok = solver.add_pin_info(int_pin_info)
    if not pin_ok:
        print("[run] Pin check failed — one or more pins are off-grid. Aborting.")
        sys.exit(1)

    solution = solver.solve()
    if solution is None:
        print("[run] No solution found.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 4. Extract geometry + scale back to physical microns
    #
    #    All three output modes receive the same scaled geometry dict so
    #    coordinates are always in µm regardless of which mode is chosen.
    # ------------------------------------------------------------------
    from z3router.io.visualizer import extract_geometry, scale_geometry

    geometry = scale_geometry(
        extract_geometry(solution, int_pin_info, tech),
        scale = tech.mfg_grid_res,
    )

    print(f"[run] Solution found. Output mode: {args.mode!r}")

    # ------------------------------------------------------------------
    # 5. Output
    # ------------------------------------------------------------------
    if args.mode == "visualize":
        _mode_visualize(geometry, tech)

    elif args.mode == "dump":
        _mode_dump(geometry, tech, args.output)

    elif args.mode == "eda":
        _mode_eda(geometry, tech, eda_width_map)


if __name__ == "__main__":
    main()
