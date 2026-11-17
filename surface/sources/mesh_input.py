from fractions import Fraction
from surface.mesh.model import OrdinaryMesh
from surface.mesh.geometry import require_nondegenerate_triangle
from surface.mesh.incidence import build_incidence


def validate_node_coordinates(nodes):
    if type(nodes) is not dict or not nodes:
        raise ValueError("nonempty node mapping required")
    result = {}
    for node, coordinates in nodes.items():
        if type(node) is not str or not node:
            raise ValueError("nonempty string node ID required")
        if (type(coordinates) not in (list, tuple) or len(coordinates) != 3
                or any(not isinstance(x, Fraction) for x in coordinates)):
            raise ValueError("coordinates must be three Fractions")
        result[node] = tuple(coordinates)
    return dict(sorted(result.items()))


def validate_face_references(faces, nodes):
    if type(faces) is not dict or not 1 <= len(faces) <= 60:
        raise ValueError("one to sixty identified faces required")
    result = {}
    for face, vertices in faces.items():
        if type(face) is not str or not face:
            raise ValueError("nonempty string face ID required")
        if (type(vertices) not in (list, tuple) or len(vertices) != 3
                or any(type(v) is not str or v not in nodes for v in vertices)
                or len(set(vertices)) != 3):
            raise ValueError("invalid triangle references")
        require_nondegenerate_triangle(*(nodes[v] for v in vertices))
        result[face] = tuple(vertices)
    return dict(sorted(result.items()))


def normalize_mesh(value):
    if type(value) is not dict or set(value) != {"nodes", "faces"}:
        raise ValueError("mesh requires nodes and faces")
    nodes = validate_node_coordinates(value["nodes"])
    faces = validate_face_references(value["faces"], nodes)
    per_face, per_edge = build_incidence(faces)
    return OrdinaryMesh(nodes, faces, per_face, per_edge)
