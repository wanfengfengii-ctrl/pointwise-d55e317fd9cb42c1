"""Geometric transfer evidence between two triangulations of one region.

The mesh side produces the saved provenance consumed by the field side:
source barycentric parameters of target nodes, target edge segmentations
through source faces, and positive-area source/target overlaps.
"""
from fractions import Fraction

from surface.mesh.planar import (PlanarFrame, barycentric_2d,
                                 clip_to_source_triangle, dedupe_polygon,
                                 lerp2, require_planar_region,
                                 segment_crossing_marks, shoelace2,
                                 signed_area2)


def build_transfer_evidence(source, target):
    """Validate both meshes and the shared region, then return evidence.

    The result is {"nodes": ..., "edges": ..., "overlaps": ...} covering every
    target node and canonical target edge.  Any geometric or coverage
    violation raises ValueError.
    """
    frame = PlanarFrame.from_mesh(source)
    source_nodes2d, source_faces2d = require_planar_region(source, frame)
    target_nodes2d, target_faces2d = require_planar_region(target, frame)
    nodes = {node: _locate(source, source_faces2d, target_nodes2d[node])
             for node in sorted(target.nodes)}
    edges = {}
    for edge in target.edges:
        edges[edge] = _edge_pieces(source, source_nodes2d, source_faces2d,
                                   target_nodes2d[edge[0]],
                                   target_nodes2d[edge[1]])
    overlaps, area2_source, area2_target = _face_overlaps(
        source, target, source_faces2d, target_faces2d)
    _require_same_region(source, target, overlaps, area2_source, area2_target)
    return {"nodes": nodes, "edges": edges, "overlaps": overlaps}


def _locate(source, source_faces2d, point):
    for face in sorted(source.faces):
        weights = barycentric_2d(*source_faces2d[face], point)
        if all(w >= 0 for w in weights):
            return {"source_face": face, "weights": weights}
    raise ValueError("target geometry leaves the source region")


def _edge_pieces(source, source_nodes2d, source_faces2d, p0, p1):
    marks = {Fraction(0), Fraction(1)}
    for q0, q1 in source.edges:
        marks |= segment_crossing_marks(p0, p1, source_nodes2d[q0],
                                        source_nodes2d[q1])
    ordered = sorted(marks)
    pieces = []
    for t0, t1 in zip(ordered, ordered[1:]):
        if t0 == t1:
            continue
        mid = (t0 + t1) / 2
        carrier = _locate(source, source_faces2d, lerp2(p0, p1, mid))
        triangle = source_faces2d[carrier["source_face"]]
        pieces.append({
            "source_face": carrier["source_face"],
            "interval": (t0, t1),
            "start_weights": barycentric_2d(*triangle, lerp2(p0, p1, t0)),
            "end_weights": barycentric_2d(*triangle, lerp2(p0, p1, t1))})
    return pieces


def _face_overlaps(source, target, source_faces2d, target_faces2d):
    overlaps = []
    area2_source = {face: signed_area2(triangle)
                    for face, triangle in source_faces2d.items()}
    area2_target = {face: signed_area2(triangle)
                    for face, triangle in target_faces2d.items()}
    for s in sorted(source.faces):
        for t in sorted(target.faces):
            weights = [barycentric_2d(*source_faces2d[s], p)
                       for p in target_faces2d[t]]
            polygon = dedupe_polygon(clip_to_source_triangle(
                [(w[1], w[2]) for w in weights]))
            if len(polygon) < 3:
                continue
            fraction = shoelace2(polygon)
            if fraction <= 0:
                continue
            overlaps.append({
                "source_face": s, "target_face": t,
                "source_weights": tuple((Fraction(1) - u - v, u, v)
                                        for u, v in polygon),
                "source_fraction": fraction})
    return overlaps, area2_source, area2_target


def _require_same_region(source, target, overlaps, area2_source, area2_target):
    covered_source = {face: Fraction(0) for face in source.faces}
    covered_target = {face: Fraction(0) for face in target.faces}
    for item in overlaps:
        s, t = item["source_face"], item["target_face"]
        covered_source[s] += item["source_fraction"]
        covered_target[t] += item["source_fraction"] * area2_source[s]
    for face, value in covered_source.items():
        if value != 1:
            raise ValueError("source region is not fully covered by the target")
    for face, value in covered_target.items():
        if value != area2_target[face]:
            raise ValueError("target region is not fully covered by the source")
