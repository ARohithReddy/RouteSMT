"""
z3router.core.variables
=======================
Creates and manages all Z3 decision variables used by the router.

Three variable families are produced:

* **Node variables** – one ``z3.Bool`` per (net, layer, x, y) grid point.
  ``True`` means the point is used in the routing solution.

* **Edge direction variables** – one ``z3.Bool`` per directed edge between
  adjacent nodes.  Used to enforce a DAG-like flow structure.

* **Edge flow variables** – one ``z3.Int`` per directed edge.
  Used in the flow-conservation constraint that proves connectivity.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import z3

from z3router.tech.layer_info import Tech
from z3router.core.grid import LayerGrid


# Type aliases  (kept simple – no heavy typing infrastructure needed)
NodeMatrix  = Dict[int, Dict[int, z3.BoolRef]]          # x -> y -> var
NetNodes    = Dict[str, Dict[str, NodeMatrix]]           # net -> layer -> matrix
NetEdges    = Dict[str, Dict[z3.BoolRef,                 # net -> node_var -> {neighbor_var: edge_var}
                             Dict[z3.BoolRef, z3.BoolRef]]]
EdgeFlows   = Dict[z3.BoolRef, z3.ArithRef]              # edge_var -> flow_int_var


def _node_var_name(net: str, layer: str, x: int, y: int) -> str:
    return f"{net}__{layer}__{x}__{y}"


def _edge_var_name(*parts: str) -> str:
    return "edge__" + "__".join(parts)


def _flow_var_name(edge_var: z3.BoolRef) -> str:
    return "flow__" + str(edge_var)


def create_node_variables(
    net: str,
    net_layers: List[str],
    layer_grids: LayerGrid,
) -> Dict[str, NodeMatrix]:
    """
    Allocate one ``z3.Bool`` per grid point for a single net.

    Parameters
    ----------
    net:
        Net name (used as part of the variable name for readability).
    net_layers:
        Layers this net is allowed to use.
    layer_grids:
        Grid coordinate lists per layer.

    Returns
    -------
    dict
        ``{ layer: { x: { y: z3.Bool } } }``
    """
    node_vars: Dict[str, NodeMatrix] = {}
    for layer in net_layers:
        xs, ys = layer_grids[layer]
        node_vars[layer] = {
            x: {y: z3.Bool(_node_var_name(net, layer, x, y)) for y in ys}
            for x in xs
        }
    return node_vars


def create_edge_variables(
    net: str,
    node_vars: Dict[str, NodeMatrix],
    layer_grids: LayerGrid,
    tech: Tech,
) -> Dict[z3.BoolRef, Dict[z3.BoolRef, z3.BoolRef]]:
    """
    Allocate directed edge ``z3.Bool`` variables for one net.

    Metal layers get edges to adjacent same-layer nodes along the routing
    direction.  Via layers get edges to the metal nodes directly above and
    below them.

    Parameters
    ----------
    net:
        Net name.
    node_vars:
        Node variable matrices (output of :func:`create_node_variables`).
    layer_grids:
        Per-layer coordinate lists.
    tech:
        Technology definition.

    Returns
    -------
    dict
        ``{ from_node_var: { to_node_var: edge_bool_var } }``
    """
    edges: Dict[z3.BoolRef, Dict[z3.BoolRef, z3.BoolRef]] = {}

    # --- same-layer edges for metal layers ---
    for layer, matrix in node_vars.items():
        if layer in tech.via_layers:
            continue
        direction = tech.routing_directions[layer]
        xs, ys = layer_grids[layer]

        for xi, x in enumerate(xs):
            x_prev = xs[xi - 1] if xi > 0 else None
            x_next = xs[xi + 1] if xi < len(xs) - 1 else None
            for yi, y in enumerate(ys):
                y_prev = ys[yi - 1] if yi > 0 else None
                y_next = ys[yi + 1] if yi < len(ys) - 1 else None

                node = matrix[x][y]
                edges.setdefault(node, {})

                if direction == "horizontal":
                    for nx, ny, label in [
                        (x_prev, y, f"{x}__{y}__{x_prev}__{y}"),
                        (x_next, y, f"{x}__{y}__{x_next}__{y}"),
                    ]:
                        if nx is not None:
                            neighbor = matrix[nx][ny]
                            edges[node][neighbor] = z3.Bool(
                                _edge_var_name(net, layer, label)
                            )

                elif direction == "vertical":
                    for nx, ny, label in [
                        (x, y_prev, f"{x}__{y}__{x}__{y_prev}"),
                        (x, y_next, f"{x}__{y}__{x}__{y_next}"),
                    ]:
                        if ny is not None:
                            neighbor = matrix[nx][ny]
                            edges[node][neighbor] = z3.Bool(
                                _edge_var_name(net, layer, label)
                            )

    # --- cross-layer edges for via layers ---
    for layer, matrix in node_vars.items():
        if layer not in tech.via_layers:
            continue
        via = tech.via_info[layer]
        layer_above = via.layer_above
        layer_below = via.layer_below
        mat_above = node_vars.get(layer_above, {})
        mat_below = node_vars.get(layer_below, {})

        for x in layer_grids[layer][0]:
            for y in layer_grids[layer][1]:
                via_node = matrix[x][y]
                edges.setdefault(via_node, {})

                # via → metal below  and  metal below → via
                if x in mat_below and y in mat_below.get(x, {}):
                    metal_below = mat_below[x][y]
                    edges.setdefault(metal_below, {})
                    label = f"{net}__{layer}__{layer_below}__{x}__{y}"
                    edges[via_node][metal_below] = z3.Bool(_edge_var_name(label))
                    edges[metal_below][via_node] = z3.Bool(_edge_var_name(label + "__rev"))

                # via → metal above  and  metal above → via
                if x in mat_above and y in mat_above.get(x, {}):
                    metal_above = mat_above[x][y]
                    edges.setdefault(metal_above, {})
                    label = f"{net}__{layer}__{layer_above}__{x}__{y}"
                    edges[via_node][metal_above] = z3.Bool(_edge_var_name(label))
                    edges[metal_above][via_node] = z3.Bool(_edge_var_name(label + "__rev"))

    return edges


def create_flow_variables(
    net_edges: Dict[z3.BoolRef, Dict[z3.BoolRef, z3.BoolRef]],
    max_flow: int,
) -> Tuple[EdgeFlows, List[z3.BoolRef]]:
    """
    Allocate one ``z3.Int`` flow variable per directed edge and emit
    basic bound constraints (0 ≤ flow ≤ max_flow).

    Parameters
    ----------
    net_edges:
        Edge variable map for one net.
    max_flow:
        Upper bound — equals the number of sinks for this net.

    Returns
    -------
    (flow_vars, bound_constraints)
    """
    flow_vars: EdgeFlows = {}
    bound_constraints: List[z3.BoolRef] = []

    seen: set = set()
    for neighbors in net_edges.values():
        for edge_var in neighbors.values():
            key = str(edge_var)
            if key in seen:
                continue
            seen.add(key)
            fv = z3.Int(_flow_var_name(edge_var))
            flow_vars[edge_var] = fv
            bound_constraints.append(fv >= 0)
            bound_constraints.append(fv <= max_flow)

    return flow_vars, bound_constraints
