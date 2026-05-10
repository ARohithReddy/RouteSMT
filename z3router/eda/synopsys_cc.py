"""
z3router.eda.synopsys_cc
========================
Synopsys Custom Compiler integration layer.

Converts a **pre-scaled geometry dict** (physical micron coordinates) into
layout shapes and writes them to an open cell view using the
``snps.custom.cmd`` API.

The geometry is produced by the shared pipeline in ``run.py``::

    geometry = scale_geometry(extract_geometry(solution, pin_info, tech),
                              scale=tech.mfg_grid_res)

so this module always receives micron values — no further scaling is done here.

Usage (inside a Custom Compiler Python session)::

    import snps.custom.cmd as cc
    from z3router.eda.synopsys_cc import write_shapes_from_geometry

    write_shapes_from_geometry(
        cell_view = cc.object.fromOa(tcl.eval('ed')),
        geometry  = geometry,          # already in microns
        tech      = tech,
        width_map = {"poly": 0.014, "tcn": 0.016, "m0": 0.020, ...},
    )
"""

from __future__ import annotations

from typing import Dict, List

from z3router.tech.layer_info import Tech

# geometry type mirrors z3router.io.visualizer.NetGeometry
NetGeometry = Dict[str, Dict[str, Dict[str, List]]]
WidthMap    = Dict[str, float]   # layer -> physical width in microns


def write_shapes_from_geometry(
    cell_view,
    geometry: NetGeometry,
    tech: Tech,
    width_map: WidthMap,
) -> None:
    """
    Write routed wires and vias into a Synopsys Custom Compiler cell view.

    All coordinates in *geometry* must already be in physical microns
    (i.e. produced by :func:`~z3router.io.visualizer.scale_geometry`).

    Parameters
    ----------
    cell_view:
        Open cell view object (``cc.object.fromOa(tcl.eval('ed'))``).
    geometry:
        Scaled geometry dict — ``{ net: { layer: { "points": [...], "segments": [...] } } }``.
        Coordinates are physical microns (floats).
    tech:
        Technology definition (used to identify via layers).
    width_map:
        Physical wire width per layer in microns,
        e.g. ``{"m0": 0.020, "m1": 0.030, "vg": 0.007}``.
    """
    try:
        import snps.custom.cmd as cc  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "Synopsys Custom Compiler Python API not available. "
            "This function requires a licensed Synopsys installation."
        ) from exc

    transaction = cc.de.startTransaction("z3router: draw route", design=cell_view)

    for net, layer_geo in geometry.items():
        for layer, geo in layer_geo.items():
            width = width_map.get(layer, 0.0)
            half_w = width / 2.0

            # Point markers → small rectangles
            for pt in geo["points"]:
                bbox = [
                    [pt[0] - half_w, pt[1] - half_w],
                    [pt[0] + half_w, pt[1] + half_w],
                ]
                cc.le.createRectangle(
                    bbox, design=cell_view, lpp=layer, net=net
                )

            # Segments → path segments (already physical microns)
            for seg in geo["segments"]:
                cc.le.createPathSeg(
                    seg, design=cell_view, width=width, lpp=layer, net=net
                )

    cc.de.endTransaction(transaction)

