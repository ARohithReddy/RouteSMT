"""
z3router.constraints.routing
=============================
All Z3 constraint generators for the SMT routing problem.

Each public function accepts the current state and returns a list of
``z3`` clauses to be added to the solver.  Nothing is stateful — all
functions are pure in the sense that they only *read* the variable
dicts and *return* constraints.

Constraint families
-------------------
``overlap``
    No two nets may occupy the same grid node on the same layer.
``connectivity``
    Every used node must be connected to its neighbors in a legal way
    (pass-through, pin extension, via, or endpoint).
``edge_direction``
    Active edges must be consistent with node activation; a node cannot
    simultaneously have an in-edge and an out-edge along the same axis.
``flow``
    Integer flow conservation proves the routing forms a *single connected
    tree* rather than disconnected fragments.
``options``
    Optional soft / hard constraints: Hanan-grid pruning, pin-extension
    restriction, trunk-count limits, and equal wire-length matching.
"""

from __future__ import annotations

import itertools
from typing import Dict, List, Optional, Tuple

import z3

from z3router.tech.layer_info import Tech
from z3router.core.grid import LayerGrid
from z3router.core.variables import NodeMatrix, NetNodes, NetEdges, EdgeFlows


# ---------------------------------------------------------------------------
# Helper: iterate grid points with prev / next neighbors
# ---------------------------------------------------------------------------

def _with_neighbors(coords: List[int]):
    """Yield (prev, current, next) for every element; boundary = None."""
    n = len(coords)
    for i, val in enumerate(coords):
        yield (coords[i - 1] if i > 0 else None,
               val,
               coords[i + 1] if i < n - 1 else None)


# ---------------------------------------------------------------------------
# 1. Overlap constraints
# ---------------------------------------------------------------------------

def build_no_overlap_constraints(
    all_node_vars: NetNodes,
) -> List[z3.BoolRef]:
    """
    Forbid two nets from sharing any grid node on the same layer.

    For every pair of nets and every shared layer, assert:
    ``NOT (node_net1[x][y] AND node_net2[x][y])``
    """
    constraints: List[z3.BoolRef] = []
    for net1, net2 in itertools.combinations(all_node_vars, 2):
        shared_layers = set(all_node_vars[net1]) & set(all_node_vars[net2])
        for layer in shared_layers:
            mat1 = all_node_vars[net1][layer]
            mat2 = all_node_vars[net2][layer]
            for x in mat1:
                if x not in mat2:
                    continue
                for y in mat1[x]:
                    if y not in mat2[x]:
                        continue
                    constraints.append(z3.Not(z3.And(mat1[x][y], mat2[x][y])))
    return constraints


# ---------------------------------------------------------------------------
# 2. Connectivity constraints
# ---------------------------------------------------------------------------

def build_connectivity_constraints(
    net: str,
    node_vars: Dict[str, NodeMatrix],
    layer_grids: LayerGrid,
    pin_info: Dict[str, List[List[int]]],
    metal_adjacency: Dict[str, Dict[str, List[str]]],
    tech: Tech,
) -> List[z3.BoolRef]:
    """
    Ensure every active node is topologically legal.

    Rules
    -----
    * **Via node**: must connect to both the metal above and metal below.
    * **Pin node**: must have at least one neighbor (same-layer or cross-layer).
    * **Interior wire node**: must satisfy one of:
        - connected to ≥ 2 same-layer neighbors (straight wire),
        - connected to vias on both sides (feed-through),
        - connected to exactly one via side + ≥ 1 same-layer neighbor (L/T turn).
    """
    constraints: List[z3.BoolRef] = []

    for layer, matrix in node_vars.items():
        is_via = layer in tech.via_layers
        if is_via:
            via_above_list = [tech.via_info[layer].layer_above]
            via_below_list = [tech.via_info[layer].layer_below]
        else:
            via_above_list = [v for v in metal_adjacency.get(layer, {}).get("above", [])
                              if v in node_vars]
            via_below_list = [v for v in metal_adjacency.get(layer, {}).get("below", [])
                              if v in node_vars]

        xs, ys = layer_grids[layer]

        for x_prev, x, x_next in _with_neighbors(xs):
            for y_prev, y, y_next in _with_neighbors(ys):
                node = matrix[x][y]
                direction = tech.routing_directions.get(layer)

                # Same-layer neighbors (along routing direction)
                same_layer_neighbors: List[z3.BoolRef] = []
                if direction == "horizontal":
                    if x_prev is not None:
                        same_layer_neighbors.append(matrix[x_prev][y])
                    if x_next is not None:
                        same_layer_neighbors.append(matrix[x_next][y])
                elif direction == "vertical":
                    if y_prev is not None:
                        same_layer_neighbors.append(matrix[x][y_prev])
                    if y_next is not None:
                        same_layer_neighbors.append(matrix[x][y_next])

                # Cross-layer neighbors (vias)
                above_neighbors: List[z3.BoolRef] = []
                for via_layer in via_above_list:
                    vm = node_vars.get(via_layer, {})
                    if x in vm and y in vm.get(x, {}):
                        above_neighbors.append(vm[x][y])

                below_neighbors: List[z3.BoolRef] = []
                for via_layer in via_below_list:
                    vm = node_vars.get(via_layer, {})
                    if x in vm and y in vm.get(x, {}):
                        below_neighbors.append(vm[x][y])

                other_layer_neighbors = above_neighbors + below_neighbors
                all_neighbors = same_layer_neighbors + other_layer_neighbors

                # ---- via layer: must connect both sides ----
                if is_via:
                    if other_layer_neighbors:
                        constraints.append(
                            z3.Or(z3.Not(node), z3.And(node, *other_layer_neighbors))
                        )
                    continue

                # ---- pin node: at least one neighbor active ----
                net_pin_locs = pin_info.get(layer, [])
                if [x, y] in net_pin_locs:
                    if all_neighbors:
                        constraints.append(z3.Or(*all_neighbors))
                    continue

                # ---- interior wire node ----
                if not all_neighbors:
                    # isolated point — cannot be used
                    constraints.append(z3.Not(node))
                    continue

                wire_cases: List[z3.BoolRef] = [z3.Not(node)]

                # Case A: pass-through (≥ 2 same-layer neighbors)
                if same_layer_neighbors:
                    wire_cases.append(
                        z3.And(node, z3.Sum(same_layer_neighbors) >= 2)
                    )

                # Case B: via feed-through (via above AND via below, no same-layer)
                if via_above_list and via_below_list and above_neighbors and below_neighbors:
                    wire_cases.append(
                        z3.And(
                            node,
                            z3.And(z3.Or(*above_neighbors), z3.Or(*below_neighbors)),
                            z3.Not(z3.Or(*same_layer_neighbors)) if same_layer_neighbors else True,
                        )
                    )

                # Case C: one via side + same-layer (L or T)
                if bool(via_above_list) ^ bool(via_below_list):
                    one_side = above_neighbors or below_neighbors
                    if one_side and same_layer_neighbors:
                        wire_cases.append(
                            z3.And(node, z3.Or(*one_side), z3.Or(*same_layer_neighbors))
                        )

                # Case D: any via + same-layer
                if other_layer_neighbors and same_layer_neighbors:
                    wire_cases.append(
                        z3.And(node, z3.Or(*other_layer_neighbors), z3.Or(*same_layer_neighbors))
                    )

                constraints.append(z3.Or(*wire_cases))

    return constraints


# ---------------------------------------------------------------------------
# 3. Edge-direction constraints
# ---------------------------------------------------------------------------

def build_edge_direction_constraints(
    net: str,
    node_vars: Dict[str, NodeMatrix],
    net_edges: Dict[z3.BoolRef, Dict[z3.BoolRef, z3.BoolRef]],
    layer_grids: LayerGrid,
) -> List[z3.BoolRef]:
    """
    Enforce consistency between node activation and edge activation.

    * A node is active iff at least one incident edge is active.
    * An edge pair (u→v, v→u) cannot *both* be active simultaneously.
    """
    constraints: List[z3.BoolRef] = []

    for layer, matrix in node_vars.items():
        for x in layer_grids[layer][0]:
            for y in layer_grids[layer][1]:
                node = matrix[x][y]
                if node not in net_edges:
                    continue

                neighbors = list(net_edges[node].keys())
                out_edges = [net_edges[node][nb] for nb in neighbors]
                in_edges  = [net_edges[nb][node]  for nb in neighbors
                             if nb in net_edges and node in net_edges[nb]]

                all_incident = out_edges + in_edges
                if not all_incident:
                    continue

                # node active  ↔  some incident edge active
                constraints.append(
                    z3.Or(
                        z3.And(z3.Not(node), z3.Not(z3.Or(*all_incident))),
                        z3.And(node, z3.Or(*all_incident)),
                    )
                )

                # no simultaneous in+out along same axis
                for in_e, out_e in zip(in_edges, out_edges):
                    constraints.append(z3.Not(z3.And(in_e, out_e)))

    return constraints


# ---------------------------------------------------------------------------
# 4. Flow constraints (connectivity proof)
# ---------------------------------------------------------------------------

def _total_flow(
    node: z3.BoolRef,
    net_edges: Dict[z3.BoolRef, Dict[z3.BoolRef, z3.BoolRef]],
    flow_vars: EdgeFlows,
    incoming: bool,
) -> z3.ArithRef:
    """Sum the flow on all edges entering (or leaving) *node*."""
    neighbors = list(net_edges.get(node, {}).keys())
    if incoming:
        edge_list = [net_edges[nb][node] for nb in neighbors
                     if nb in net_edges and node in net_edges[nb]]
    else:
        edge_list = [net_edges[node][nb] for nb in neighbors]
    return z3.Sum([flow_vars[e] for e in edge_list if e in flow_vars])


def build_flow_constraints(
    net: str,
    node_vars: Dict[str, NodeMatrix],
    net_edges: Dict[z3.BoolRef, Dict[z3.BoolRef, z3.BoolRef]],
    flow_vars: EdgeFlows,
    flow_bound_constraints: List[z3.BoolRef],
    pin_info: Dict[str, List[List[int]]],
    source: List,          # [layer, x, y]
    total_source_flow: int,
    layer_grids: LayerGrid,
    tech: Tech,
) -> List[z3.BoolRef]:
    """
    Add flow-conservation constraints that prove global connectivity.

    * Source node emits ``total_source_flow`` units.
    * Each sink node absorbs exactly 1 unit.
    * Every internal (non-pin) active node is flow-balanced.

    Also adds edge activation constraints: an edge between two active
    nodes must carry non-zero flow.
    """
    constraints: List[z3.BoolRef] = list(flow_bound_constraints)

    # Edge activation ↔ non-zero flow
    for node, neighbors in net_edges.items():
        for neighbor, edge_var in neighbors.items():
            if edge_var not in flow_vars:
                continue
            fv = flow_vars[edge_var]
            # If neither node is active → edge inactive, flow = 0
            constraints.append(z3.Or(node, neighbor,
                                     z3.Not(edge_var)))
            constraints.append(z3.Or(node, neighbor,
                                     fv == 0))
            # If both active → edge must carry flow
            constraints.append(z3.Or(z3.Not(z3.And(node, neighbor)),
                                     fv != 0))

    # Flow conservation per node
    for layer, matrix in node_vars.items():
        for x in layer_grids[layer][0]:
            for y in layer_grids[layer][1]:
                node = matrix[x][y]
                net_pin_locs = pin_info.get(layer, [])
                is_pin = [x, y] in net_pin_locs
                is_source = [layer, x, y] == source

                out_flow = _total_flow(node, net_edges, flow_vars, incoming=False)
                in_flow  = _total_flow(node, net_edges, flow_vars, incoming=True)

                if is_pin and is_source:
                    constraints.append(out_flow - in_flow == total_source_flow)
                elif is_pin:
                    constraints.append(in_flow - out_flow == 1)
                else:
                    # Internal node: balanced when active
                    constraints.append(
                        z3.Or(z3.Not(node),
                              z3.And(node, in_flow == out_flow))
                    )

    return constraints


# ---------------------------------------------------------------------------
# 5. Option constraints
# ---------------------------------------------------------------------------

def build_hanan_grid_constraints(
    net: str,
    node_vars: Dict[str, NodeMatrix],
    pin_info: Dict[str, List[List[int]]],
    layer_grids: LayerGrid,
    tech: Tech,
    closest_track_count: int = 3,
) -> List[z3.BoolRef]:
    """
    Restrict routing to Hanan-grid tracks near pins, reducing search space.

    For each net and each metal layer, only keep the *closest_track_count*
    tracks nearest to each pin.  All other tracks are forced to zero.
    """
    constraints: List[z3.BoolRef] = []

    all_pin_xy = [xy for locs in pin_info.values() for xy in locs]

    def _closest_tracks(track_list: List[int], coord: int, limit: int) -> List[int]:
        ranked = sorted(track_list, key=lambda t: abs(t - coord))
        return ranked[:limit]

    for layer, matrix in node_vars.items():
        if layer in tech.via_layers:
            continue
        is_horizontal = tech.routing_directions[layer] == "horizontal"
        xs, ys = layer_grids[layer]

        allowed_tracks: set[int] = set()
        for pin_x, pin_y in all_pin_xy:
            if is_horizontal:
                allowed_tracks.update(_closest_tracks(ys, pin_y, closest_track_count))
            else:
                allowed_tracks.update(_closest_tracks(xs, pin_x, closest_track_count))

        for x in xs:
            for y in ys:
                track = y if is_horizontal else x
                if track not in allowed_tracks:
                    constraints.append(z3.Not(matrix[x][y]))

    return constraints


def build_pin_extension_constraints(
    net: str,
    node_vars: Dict[str, NodeMatrix],
    pin_info: Dict[str, List[List[int]]],
    layer_grids: LayerGrid,
    metal_adjacency: Dict[str, Dict[str, List[str]]],
    tech: Tech,
    max_extension: int = 2,
) -> List[z3.BoolRef]:
    """
    Restrict specified layers to short stubs used only for pin access.

    On a "pin-extension-only" layer, routing is allowed only on the
    track that contains the pin and within *max_extension* grid steps
    of each pin's perpendicular coordinate.  The via layer immediately
    above is restricted by the same rule.
    """
    constraints: List[z3.BoolRef] = []

    for layer, matrix in node_vars.items():
        if layer not in pin_info:
            continue
        direction = tech.routing_directions.get(layer)

        # Find the via directly above this layer (if any)
        via_above = (metal_adjacency.get(layer, {}).get("above", [None]) or [None])[0]
        mat_above = node_vars.get(via_above, {}) if via_above else {}

        xs, ys = layer_grids[layer]

        pin_x_set = {pt[0] for pt in pin_info[layer]}
        pin_y_set = {pt[1] for pt in pin_info[layer]}

        # Step 1: restrict to the correct track line
        for x in xs:
            for y in ys:
                if direction == "horizontal" and y not in pin_y_set:
                    constraints.append(z3.Not(matrix[x][y]))
                    if x in mat_above and y in mat_above.get(x, {}):
                        constraints.append(z3.Not(mat_above[x][y]))
                elif direction == "vertical" and x not in pin_x_set:
                    constraints.append(z3.Not(matrix[x][y]))
                    if x in mat_above and y in mat_above.get(x, {}):
                        constraints.append(z3.Not(mat_above[x][y]))

        # Step 2: restrict to within max_extension steps of each pin
        if direction == "horizontal":
            for y in ys:
                pins_on_track = [pt[0] for pt in pin_info[layer] if pt[1] == y]
                allowed_x_indices: set[int] = set()
                for px in pins_on_track:
                    if px in xs:
                        pi = xs.index(px)
                        for delta in range(-max_extension, max_extension + 1):
                            if 0 <= pi + delta < len(xs):
                                allowed_x_indices.add(pi + delta)
                for xi, x in enumerate(xs):
                    if xi not in allowed_x_indices:
                        constraints.append(z3.Not(matrix[x][y]))
                        if x in mat_above and y in mat_above.get(x, {}):
                            constraints.append(z3.Not(mat_above[x][y]))

        elif direction == "vertical":
            for x in xs:
                pins_on_track = [pt[1] for pt in pin_info[layer] if pt[0] == x]
                allowed_y_indices: set[int] = set()
                for py in pins_on_track:
                    if py in ys:
                        pi = ys.index(py)
                        for delta in range(-max_extension, max_extension + 1):
                            if 0 <= pi + delta < len(ys):
                                allowed_y_indices.add(pi + delta)
                for yi, y in enumerate(ys):
                    if yi not in allowed_y_indices:
                        constraints.append(z3.Not(matrix[x][y]))
                        if x in mat_above and y in mat_above.get(x, {}):
                            constraints.append(z3.Not(mat_above[x][y]))

    return constraints


def build_max_trunks_constraints(
    net: str,
    node_vars: Dict[str, NodeMatrix],
    layer_grids: LayerGrid,
    metal_adjacency: Dict[str, Dict[str, List[str]]],
    tech: Tech,
    max_trunks_per_layer: Dict[str, int],
    no_adjacent_trunks: bool = True,
) -> List[z3.BoolRef]:
    """
    Limit how many routing tracks a net may use per layer.

    Optionally also forbids two consecutive occupied tracks
    (``no_adjacent_trunks=True``).
    """
    constraints: List[z3.BoolRef] = []

    for layer, max_trunks in max_trunks_per_layer.items():
        if layer not in node_vars:
            continue
        matrix = node_vars[layer]
        xs, ys = layer_grids[layer]
        direction = tech.routing_directions[layer]

        via_above = (metal_adjacency.get(layer, {}).get("above", [None]) or [None])[0]
        via_below = (metal_adjacency.get(layer, {}).get("below", [None]) or [None])[0]
        mat_above = node_vars.get(via_above, {}) if via_above else {}
        mat_below = node_vars.get(via_below, {}) if via_below else {}

        trunk_used: List[z3.BoolRef] = []  # one bool per track index

        if direction == "horizontal":
            for x in xs:
                constraints.append(z3.Sum([matrix[x][y] for y in ys]) <= max_trunks)
                if mat_above:
                    constraints.append(z3.Sum([mat_above.get(x, {}).get(y, False) for y in ys]) <= max_trunks)
                if mat_below:
                    constraints.append(z3.Sum([mat_below.get(x, {}).get(y, False) for y in ys]) <= max_trunks)
            for y in ys:
                trunk_used.append(z3.Or(*[matrix[x][y] for x in xs]))

        elif direction == "vertical":
            for y in ys:
                constraints.append(z3.Sum([matrix[x][y] for x in xs]) <= max_trunks)
                if mat_above:
                    constraints.append(z3.Sum([mat_above.get(x, {}).get(y, False) for x in xs]) <= max_trunks)
                if mat_below:
                    constraints.append(z3.Sum([mat_below.get(x, {}).get(y, False) for x in xs]) <= max_trunks)
            for x in xs:
                trunk_used.append(z3.Or(*[matrix[x][y] for y in ys]))

        constraints.append(z3.Sum(trunk_used) <= max_trunks)

        if no_adjacent_trunks:
            for t1, t2 in zip(trunk_used[:-1], trunk_used[1:]):
                constraints.append(z3.Not(z3.And(t1, t2)))

    return constraints


def _as_int(b: z3.BoolRef) -> z3.ArithRef:
    """
    Coerce a Z3 ``BoolRef`` to an integer ``ArithRef`` (0 or 1).

    Using ``z3.If`` is explicit and safe — it avoids relying on Z3's
    implicit Bool-to-Int coercion, which can produce unexpected sort errors
    in some solver versions when Bool variables appear in arithmetic sums.
    """
    return z3.If(b, 1, 0)


def build_equal_wire_length_constraints(
    all_node_vars: NetNodes,
    layer_grids: LayerGrid,
    tech: Tech,
    equal_length_groups: Dict[str, List[List[str]]],
) -> List[z3.BoolRef]:
    """
    Force equal physical wire length for groups of nets on a layer.

    Wire length is computed in grid units as the sum of activated segment
    lengths, where each segment's length is the coordinate difference between
    two adjacent active nodes.  This correctly handles non-uniform track
    spacing (unlike a simple active-node count).

    For a **horizontal** layer, wires run along X.  A segment on Y-track ``y``
    between columns ``x_prev`` and ``x`` contributes ``(x - x_prev)`` units
    when *both* ``node[x_prev][y]`` and ``node[x][y]`` are active::

        wire_length = Σ  (x - x_prev) · node[x_prev][y] · node[x][y]
                     x,y pairs

    For a **vertical** layer the same logic applies along Y.

    Parameters
    ----------
    all_node_vars:
        Node variable matrices for every net and layer.
    layer_grids:
        Per-layer ``(xs, ys)`` coordinate lists (integer grid units).
    tech:
        Technology definition (provides routing directions and via list).
    equal_length_groups:
        ``{ layer: [ [net1, net2, ...], [netA, netB], ... ] }``
        Each inner list is a group of nets that must have equal wire length
        on that layer.  Via layers are silently skipped.

    Returns
    -------
    List[z3.BoolRef]
        Equality constraints ``wire_length(net1) == wire_length(net2)``
        for every pair within each group.
    """
    constraints: List[z3.BoolRef] = []

    for layer, net_groups in equal_length_groups.items():

        # Via layers have no routing direction — skip silently.
        direction = tech.routing_directions.get(layer)
        if direction is None or layer in tech.via_layers:
            continue

        xs, ys = layer_grids[layer]

        for group in net_groups:
            wire_length_exprs: List[z3.ArithRef] = []

            for net in group:
                matrix = all_node_vars.get(net, {}).get(layer, {})

                # Net does not use this layer — its wire length is zero.
                if not matrix:
                    wire_length_exprs.append(z3.IntVal(0))
                    continue

                segment_terms: List[z3.ArithRef] = []

                if direction == "horizontal":
                    # Wires run along X; iterate consecutive column pairs per Y-track.
                    for y in ys:
                        for x_prev, x in zip(xs[:-1], xs[1:]):
                            # Both nodes must exist in the matrix (they always do
                            # if the grid was built correctly, but guard anyway).
                            if (x_prev in matrix and y in matrix[x_prev]
                                    and x in matrix and y in matrix[x]):
                                seg_len = x - x_prev          # integer grid units
                                term = (seg_len
                                        * _as_int(matrix[x_prev][y])
                                        * _as_int(matrix[x][y]))
                                segment_terms.append(term)

                elif direction == "vertical":
                    # Wires run along Y; iterate consecutive row pairs per X-track.
                    for x in xs:
                        for y_prev, y in zip(ys[:-1], ys[1:]):
                            if (x in matrix and y_prev in matrix[x]
                                    and y in matrix[x]):
                                seg_len = y - y_prev          # integer grid units
                                term = (seg_len
                                        * _as_int(matrix[x][y_prev])
                                        * _as_int(matrix[x][y]))
                                segment_terms.append(term)

                total = z3.Sum(segment_terms) if segment_terms else z3.IntVal(0)
                wire_length_exprs.append(total)

            # Constrain every pair of nets in the group to equal wire length.
            for expr1, expr2 in itertools.combinations(wire_length_exprs, 2):
                constraints.append(expr1 == expr2)

    return constraints
