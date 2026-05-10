
"""
z3router
========
A Z3-based SMT solver for multi-net VLSI routing.

The router formulates routing as a satisfiability / optimization problem:

* **Boolean node variables** — one per (net, layer, x, y) grid point.
* **Integer flow variables** — prove each net forms a single connected tree.
* **Z3 Optimize** — minimizes total wire usage (node count).

Quick start
-----------
::

    from z3router import RouteSolver
    from z3router.tech.layer_info import DEFAULT_TECH
    from z3router.io.visualizer import extract_geometry, plot_solution

    solver = RouteSolver(
        layer_order   = ["poly", "vg", "tcn", "vt", "m0", "m1"],
        net_layer_map = {
            "netA": ["poly", "vg", "tcn", "vt", "m0", "m1"],
            "netB": ["poly", "vg", "tcn", "vt", "m0", "m1"],
        },
        track_info = {
            "poly": list(range(7)),
            "tcn":  list(range(7)),
            "m0":   list(range(7)),
            "m1":   list(range(7)),
        },
        options = {
            "use_hanan_grid":        True,
            "pin_extension_layers":  ["poly", "tcn"],
        },
    )

    solver.add_pin_info({
        "netA": {"tcn":  [[1, 2], [3, 4]]},
        "netB": {"poly": [[4, 4], [2, 2]]},
    })

    solution = solver.solve()
    if solution:
        geometry = extract_geometry(solution, pin_info, DEFAULT_TECH)
        plot_solution(geometry, DEFAULT_TECH)
"""

from z3router.core.solver import RouteSolver
from z3router.tech.layer_info import DEFAULT_TECH, Tech, ViaInfo, LayerVisualization

__version__ = "0.1.0"
__author__  = "z3router contributors"

__all__ = [
    "RouteSolver",
    "DEFAULT_TECH",
    "Tech",
    "ViaInfo",
    "LayerVisualization",
]
