"""
z3router.core.solver
====================
``RouteSolver`` — the top-level entry point for the Z3-based router.

Typical usage::

    from z3router import RouteSolver
    from z3router.tech.layer_info import DEFAULT_TECH

    solver = RouteSolver(
        layer_order   = ["poly", "vg", "tcn", "vt", "m0", "m1"],
        net_layer_map = {"netA": ["poly", "vg", "m0"],
                         "netB": ["tcn",  "vt",  "m0"]},
        track_info    = {"poly": list(range(7)),
                         "tcn":  list(range(7)),
                         "m0":   list(range(7))},
        options       = {"use_hanan_grid": True,
                         "pin_extension_layers": ["poly", "tcn"]},
    )
    solver.add_pin_info({
        "netA": {"tcn":  [[1, 2], [3, 4]]},
        "netB": {"poly": [[4, 4], [2, 2]]},
    })
    solution = solver.solve()
"""

from __future__ import annotations

import itertools
import time
from typing import Dict, List, Optional

import z3

from z3router.tech.layer_info import Tech, DEFAULT_TECH
from z3router.core.grid import (
    build_global_xy,
    build_layer_grids,
    check_pins_on_grid,
    LayerGrid,
)
from z3router.core.variables import (
    create_node_variables,
    create_edge_variables,
    create_flow_variables,
    NodeMatrix,
    NetNodes,
    NetEdges,
    EdgeFlows,
)
from z3router.constraints.routing import (
    build_no_overlap_constraints,
    build_connectivity_constraints,
    build_edge_direction_constraints,
    build_flow_constraints,
    build_hanan_grid_constraints,
    build_pin_extension_constraints,
    build_max_trunks_constraints,
    build_equal_wire_length_constraints,
)


# Route solution type:  net -> layer -> { x: { y: bool } }
RouteSolution = Dict[str, Dict[str, Dict[int, Dict[int, bool]]]]

# Pin info type:  net -> layer -> [[x, y], ...]
PinInfo = Dict[str, Dict[str, List[List[int]]]]


class RouteSolver:
    """
    SMT-based multi-net router using Z3.

    The router formulates wire routing as a satisfiability / optimization
    problem: Boolean node variables are placed on a per-layer routing grid,
    and integer flow variables prove that the resulting graph is a single
    connected Steiner tree for each net.  The optimizer minimizes total
    wire usage (node count).

    Parameters
    ----------
    layer_order:
        Ordered list of all layers (metals + vias) available in this run.
    net_layer_map:
        ``{ net_name: [allowed_layer, ...] }``
    track_info:
        ``{ layer: [track_coord, ...] }`` — integer manufacturing-grid units.
    tech:
        Technology definition.  Defaults to ``DEFAULT_TECH``.
    options:
        Routing options dict.  Supported keys:

        * ``"use_hanan_grid"`` *(bool)* — restrict to Hanan-grid tracks.
        * ``"pin_extension_layers"`` *(list[str])* — layers that may only
          be used for short pin stubs.
        * ``"max_trunks_per_net"`` *(dict[str, int])* — ``{layer: max_count}``.
        * ``"equal_wire_length"`` *(dict)* — ``{layer: [[net1, net2], ...]}``.
    """

    def __init__(
        self,
        layer_order: List[str],
        net_layer_map: Dict[str, List[str]],
        track_info: Dict[str, List[int]],
        tech: Tech = DEFAULT_TECH,
        options: Optional[Dict] = None,
    ):
        self.layer_order   = layer_order
        self.net_layer_map = net_layer_map
        self.track_info    = track_info
        self.tech          = tech
        self.options       = options or {}

        # Populated by add_pin_info()
        self._pin_info:   PinInfo   = {}
        self._layer_grids: LayerGrid = {}

        # Z3 variable stores (populated during solve())
        self._node_vars: NetNodes  = {}
        self._net_edges: NetEdges  = {}
        self._flow_vars: EdgeFlows = {}

        # Flow metadata per net: source pin + total flow amount
        self._flow_meta: Dict[str, Dict] = {}

        # Pre-compute metal-layer adjacency from tech
        self._metal_adj = tech.build_metal_adjacency()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_pin_info(self, pin_info: PinInfo) -> bool:
        """
        Register pin locations and build the routing grid.

        Must be called before :meth:`solve`.

        Parameters
        ----------
        pin_info:
            ``{ net: { layer: [[x, y], ...] } }``
            Coordinates must already be on the manufacturing grid.

        Returns
        -------
        bool
            ``True`` if all pins are on the grid; ``False`` otherwise
            (routing will be aborted in :meth:`solve`).
        """
        self._pin_info = pin_info
        global_xy = build_global_xy(self.track_info, pin_info, self.tech)
        self._layer_grids = build_layer_grids(
            self.layer_order, self.track_info, global_xy, self.tech
        )
        return check_pins_on_grid(pin_info, self._layer_grids, self.tech)

    def solve(self) -> Optional[RouteSolution]:
        """
        Build all constraints, run the Z3 optimizer, and return the solution.

        Returns
        -------
        RouteSolution or None
            ``{ net: { layer: { x: { y: bool } } } }`` on success,
            ``None`` if unsatisfiable or if :meth:`add_pin_info` was not called.
        """
        if not self._pin_info:
            print("[z3router] No pin info registered — call add_pin_info() first.")
            return None

        t_start = time.time()
        constraints = self._build_all_constraints()
        t_constraints = time.time()

        print(f"[z3router] Constraints built in {t_constraints - t_start:.2f}s "
              f"({len(constraints)} clauses)")

        model = self._run_optimizer(constraints)
        t_solve = time.time()

        print(f"[z3router] Solver finished in {t_solve - t_constraints:.2f}s")

        if model is None:
            print("[z3router] No solution found.")
            return None

        return self._extract_solution(model)

    # ------------------------------------------------------------------
    # Internal: variable & constraint construction
    # ------------------------------------------------------------------

    def _build_all_constraints(self) -> List[z3.BoolRef]:
        constraints: List[z3.BoolRef] = []

        # 1. Create node variables + pin-forced constraints
        for net, layers in self.net_layer_map.items():
            self._node_vars[net] = create_node_variables(
                net, layers, self._layer_grids
            )
        constraints += self._pin_constraints()

        # 2. No-overlap between nets
        constraints += build_no_overlap_constraints(self._node_vars)

        # 3. Connectivity per net
        for net, node_vars in self._node_vars.items():
            constraints += build_connectivity_constraints(
                net, node_vars, self._layer_grids,
                self._pin_info.get(net, {}),
                self._metal_adj, self.tech,
            )

        # 4. Directed edge variables + edge-direction constraints
        for net, node_vars in self._node_vars.items():
            self._net_edges[net] = create_edge_variables(
                net, node_vars, self._layer_grids, self.tech
            )
            constraints += build_edge_direction_constraints(
                net, node_vars, self._net_edges[net], self._layer_grids
            )

        # 5. Flow variables + flow-conservation constraints
        for net, node_vars in self._node_vars.items():
            source, total_flow = self._compute_flow_metadata(net)
            self._flow_meta[net] = {"source": source, "total_flow": total_flow}
            flow_vars, flow_bounds = create_flow_variables(
                self._net_edges[net], total_flow
            )
            self._flow_vars.update(flow_vars)
            constraints += build_flow_constraints(
                net, node_vars, self._net_edges[net], flow_vars, flow_bounds,
                self._pin_info.get(net, {}), source, total_flow,
                self._layer_grids, self.tech,
            )

        # 6. Optional constraints
        constraints += self._build_option_constraints()

        return constraints

    def _pin_constraints(self) -> List[z3.BoolRef]:
        """Force every pin node True; forbid same location for other nets."""
        constraints: List[z3.BoolRef] = []
        for net, net_pins in self._pin_info.items():
            for layer, xy_list in net_pins.items():
                for x, y in xy_list:
                    node = self._node_vars[net][layer][x][y]
                    constraints.append(node)  # pin must be used

        # Mutual exclusion: pin of netA cannot be used by netB
        for net, net_pins in self._pin_info.items():
            for layer, xy_list in net_pins.items():
                for x, y in xy_list:
                    for other_net, other_vars in self._node_vars.items():
                        if other_net == net:
                            continue
                        if layer in other_vars and x in other_vars[layer] \
                                and y in other_vars[layer][x]:
                            constraints.append(z3.Not(other_vars[layer][x][y]))
        return constraints

    def _compute_flow_metadata(self, net: str):
        """Return (source_triple, num_sinks) for *net*."""
        source = None
        num_sinks = 0
        first = True
        for layer, xy_list in self._pin_info.get(net, {}).items():
            for x, y in xy_list:
                if first:
                    source = [layer, x, y]
                    first = False
                else:
                    num_sinks += 1
        return source, num_sinks

    def _build_option_constraints(self) -> List[z3.BoolRef]:
        constraints: List[z3.BoolRef] = []

        if self.options.get("use_hanan_grid", False):
            for net, node_vars in self._node_vars.items():
                constraints += build_hanan_grid_constraints(
                    net, node_vars,
                    self._pin_info.get(net, {}),
                    self._layer_grids, self.tech,
                )

        for layer in self.options.get("pin_extension_layers", []):
            for net, node_vars in self._node_vars.items():
                if layer in node_vars and layer in self._pin_info.get(net, {}):
                    constraints += build_pin_extension_constraints(
                        net, node_vars,
                        self._pin_info.get(net, {}),
                        self._layer_grids, self._metal_adj, self.tech,
                    )

        if "max_trunks_per_net" in self.options:
            for net, node_vars in self._node_vars.items():
                constraints += build_max_trunks_constraints(
                    net, node_vars,
                    self._layer_grids, self._metal_adj, self.tech,
                    self.options["max_trunks_per_net"],
                )

        if "equal_wire_length" in self.options:
            constraints += build_equal_wire_length_constraints(
                self._node_vars,
                self._layer_grids,
                self.tech,
                self.options["equal_wire_length"],
            )

        return constraints

    # ------------------------------------------------------------------
    # Internal: solve + extract
    # ------------------------------------------------------------------

    def _run_optimizer(self, constraints: List[z3.BoolRef]) -> Optional[z3.ModelRef]:
        """Run Z3 Optimize (minimize wire usage) and return the model."""
        optimizer = z3.Optimize()
        for clause in constraints:
            optimizer.add(clause)

        # Minimize total number of used wire nodes (vias excluded)
        wire_nodes: List[z3.BoolRef] = []
        for net, node_vars in self._node_vars.items():
            for layer, matrix in node_vars.items():
                if layer in self.tech.via_layers:
                    continue
                for x in matrix:
                    wire_nodes.extend(matrix[x].values())

        optimizer.minimize(z3.Sum(wire_nodes))

        print(f"[z3router] Running optimizer on {len(wire_nodes)} wire node variables …")
        status = optimizer.check()
        if str(status) == "sat":
            model = optimizer.model()
            print(f"[z3router] SAT — model has {len(model)} variables")
            return model

        print("[z3router] UNSAT")
        return None

    def _extract_solution(self, model: z3.ModelRef) -> RouteSolution:
        """Convert the Z3 model into a plain Python dict."""
        solution: RouteSolution = {}
        for net, node_vars in self._node_vars.items():
            solution[net] = {}
            for layer, matrix in node_vars.items():
                solution[net][layer] = {
                    x: {y: bool(model[matrix[x][y]]) for y in matrix[x]}
                    for x in matrix
                }
        return solution
