from fractions import Fraction


def face_circulation(bundle, face):
    return sum((term.sign * bundle.values.edge_integrals[term.edge]
                for term in bundle.mesh.face_edges[face]), Fraction(0))


def all_circulations(bundle):
    return {face: face_circulation(bundle, face) for face in sorted(bundle.mesh.faces)}
