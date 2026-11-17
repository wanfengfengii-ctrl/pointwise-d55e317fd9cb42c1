"""Exact planar-region rules for retriangulation plans.

All predicates are computed with Fraction arithmetic in a rational 2D frame
of the common plane, so containment, intersection and area ratios are exact.
These constraints belong only to the retriangulated-whitney-v1 convention;
ordinary V1 inputs keep their historical freedom.
"""
from fractions import Fraction

from surface.mesh.geometry import cross3, triangle_area_vector

_ZERO = Fraction(0)
_ONE = Fraction(1)


def sub3(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot3(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def sub2(a, b):
    return (a[0] - b[0], a[1] - b[1])


def cross2(u, v):
    return u[0] * v[1] - u[1] * v[0]


def dot2(u, v):
    return u[0] * v[0] + u[1] * v[1]


def lerp2(p0, p1, t):
    return (p0[0] + t * (p1[0] - p0[0]), p0[1] + t * (p1[1] - p0[1]))


class PlanarFrame:
    """Rational 2D coordinate frame of the common plane.

    project() is linear, so 2D signed areas keep the sign of the 3D winding
    with respect to the frame normal and area ratios stay exact.
    """

    def __init__(self, origin, normal, axis1, axis2):
        self.origin = origin
        self.normal = normal
        self.axis1 = axis1
        self.axis2 = axis2

    @classmethod
    def from_mesh(cls, mesh):
        face = mesh.faces[sorted(mesh.faces)[0]]
        a, b, c = (mesh.nodes[node] for node in face)
        normal = triangle_area_vector(a, b, c)
        axis1 = sub3(b, a)
        axis2 = cross3(normal, axis1)
        return cls(a, normal, axis1, axis2)

    def project(self, point):
        d = sub3(point, self.origin)
        return (dot3(d, self.axis1), dot3(d, self.axis2))

    def require_coplanar(self, point):
        if dot3(sub3(point, self.origin), self.normal) != 0:
            raise ValueError("node lies outside the common plane")


def signed_area2(triangle2d):
    a, b, c = triangle2d
    return cross2(sub2(b, a), sub2(c, a))


def barycentric_2d(a, b, c, p):
    d = cross2(sub2(b, a), sub2(c, a))
    if d == 0:
        raise ValueError("degenerate reference triangle")
    l1 = cross2(sub2(p, a), sub2(c, a)) / d
    l2 = cross2(sub2(b, a), sub2(p, a)) / d
    return (_ONE - l1 - l2, l1, l2)


_REFERENCE_EDGES = (
    ((_ZERO, _ZERO), (_ONE, _ZERO)),
    ((_ONE, _ZERO), (_ZERO, _ONE)),
    ((_ZERO, _ONE), (_ZERO, _ZERO)),
)


def _clip_halfplane(polygon, a, b):
    if not polygon:
        return []
    result = []
    previous = polygon[-1]
    previous_c = cross2(sub2(b, a), sub2(previous, a))
    for current in polygon:
        current_c = cross2(sub2(b, a), sub2(current, a))
        if (previous_c >= 0) != (current_c >= 0):
            t = previous_c / (previous_c - current_c)
            result.append((previous[0] + t * (current[0] - previous[0]),
                           previous[1] + t * (current[1] - previous[1])))
        if current_c >= 0:
            result.append(current)
        previous, previous_c = current, current_c
    return result


def clip_to_source_triangle(polygon_uv):
    """Clip a convex polygon in (lambda1, lambda2) space to the reference triangle."""
    for a, b in _REFERENCE_EDGES:
        polygon_uv = _clip_halfplane(polygon_uv, a, b)
        if not polygon_uv:
            break
    return polygon_uv


def dedupe_polygon(polygon):
    result = []
    for point in polygon:
        if not result or result[-1] != point:
            result.append(point)
    if len(result) > 1 and result[0] == result[-1]:
        result.pop()
    return result


def shoelace2(polygon):
    return sum((cross2(polygon[i], polygon[(i + 1) % len(polygon)])
                for i in range(len(polygon))), _ZERO)


def segment_crossing_marks(p0, p1, q0, q1):
    """Parameters in [0,1] where segment q0-q1 meets segment p0-p1."""
    d = sub2(p1, p0)
    e = sub2(q1, q0)
    denom = cross2(d, e)
    if denom != 0:
        t = cross2(sub2(q0, p0), e) / denom
        s = cross2(sub2(q0, p0), d) / denom
        if _ZERO <= t <= _ONE and _ZERO <= s <= _ONE:
            return {t}
        return set()
    if cross2(sub2(q0, p0), d) != 0:
        return set()
    dd = dot2(d, d)
    t0 = dot2(sub2(q0, p0), d) / dd
    t1 = dot2(sub2(q1, p0), d) / dd
    lo, hi = min(t0, t1), max(t0, t1)
    a, b = max(lo, _ZERO), min(hi, _ONE)
    if a > b:
        return set()
    return {a, b}


def require_planar_region(mesh, frame):
    """One legal triangulation of a hole-free simple planar region.

    Returns (projected_nodes, projected_faces) for downstream exact work.
    """
    projected_nodes = {}
    for node, point in mesh.nodes.items():
        frame.require_coplanar(point)
        projected_nodes[node] = frame.project(point)
    positions = {}
    for node, point in mesh.nodes.items():
        other = positions.setdefault(point, node)
        if other != node:
            raise ValueError("coincident node identities in one mesh")
    used = set()
    projected_faces = {}
    for face, vertices in mesh.faces.items():
        used.update(vertices)
        triangle = tuple(projected_nodes[v] for v in vertices)
        if signed_area2(triangle) <= 0:
            raise ValueError("face winding conflicts with the common normal")
        projected_faces[face] = triangle
    if used != set(mesh.nodes):
        raise ValueError("every node must be used by at least one face")
    _require_pairwise_legality(projected_faces)
    _require_edge_connected(mesh)
    _require_simple_boundary(mesh)
    return projected_nodes, projected_faces


def _require_pairwise_legality(projected_faces):
    reference = {(_ZERO, _ZERO), (_ONE, _ZERO), (_ZERO, _ONE)}
    faces = sorted(projected_faces)
    for i, f in enumerate(faces):
        a, b, c = projected_faces[f]
        for g in faces[i + 1:]:
            weights = [barycentric_2d(a, b, c, p) for p in projected_faces[g]]
            polygon = dedupe_polygon(clip_to_source_triangle(
                [(w[1], w[2]) for w in weights]))
            if not polygon:
                continue
            if shoelace2(polygon) != 0:
                raise ValueError("triangles share positive area")
            distinct = set(polygon)
            if len(distinct) > 2:
                raise ValueError("triangles share a partial edge chain")
            corners = {(w[1], w[2]) for w in weights}
            for point in distinct:
                if point not in reference or point not in corners:
                    raise ValueError("triangles touch outside shared vertices")


def _require_edge_connected(mesh):
    adjacency = {face: set() for face in mesh.faces}
    for occurrences in mesh.incidence.values():
        if len(occurrences) == 2:
            (first, _), (second, _) = occurrences
            adjacency[first].add(second)
            adjacency[second].add(first)
    seen = set()
    stack = [sorted(mesh.faces)[0]]
    while stack:
        face = stack.pop()
        if face in seen:
            continue
        seen.add(face)
        stack.extend(adjacency[face] - seen)
    if seen != set(mesh.faces):
        raise ValueError("faces are not connected through shared edges")


def _require_simple_boundary(mesh):
    boundary = [edge for edge, occurrences in mesh.incidence.items()
                if len(occurrences) == 1]
    vertex_edges = {}
    for edge in boundary:
        for node in edge:
            vertex_edges.setdefault(node, []).append(edge)
    if (len(boundary) < 3
            or any(len(edges) != 2 for edges in vertex_edges.values())):
        raise ValueError("boundary must be one simple closed chain")
    start = boundary[0]
    visited = {start}
    current_edge, current_node = start, start[1]
    while True:
        following = [edge for edge in vertex_edges[current_node]
                     if edge != current_edge]
        nxt = following[0]
        if nxt in visited:
            break
        visited.add(nxt)
        current_node = nxt[0] if nxt[1] == current_node else nxt[1]
        current_edge = nxt
    if len(visited) != len(boundary):
        raise ValueError("boundary must be one simple closed chain")
