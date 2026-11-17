from surface.mesh.model import FaceEdge


def canonical_edge(a, b):
    if not isinstance(a, str) or not isinstance(b, str) or a == b:
        raise ValueError("edge needs distinct node IDs")
    return (a, b) if a < b else (b, a)


def face_edge_terms(vertices):
    return tuple(FaceEdge(canonical_edge(a, b), 1 if a < b else -1)
                 for a, b in zip(vertices, vertices[1:] + vertices[:1]))


def build_incidence(faces):
    per_face, per_edge = {}, {}
    for face in sorted(faces):
        terms = face_edge_terms(tuple(faces[face]))
        per_face[face] = terms
        for term in terms:
            per_edge.setdefault(term.edge, []).append((face, term.sign))
            if len(per_edge[term.edge]) > 2:
                raise ValueError("ordinary edge has more than two incident faces")
    return per_face, {edge: tuple(items) for edge, items in sorted(per_edge.items())}
