"""Field transfer consuming the saved geometric evidence.

Node scalars are interpolated from the source piecewise-linear node field,
edge integrals are summed per segment with the source Whitney one-form
omega = sum(i<j) eij (li dlj - lj dli), and face totals move by exact
source-area fractions.  No endpoint-scalar differences, no renormalization.
"""
from fractions import Fraction

from surface.fields.bindings import FieldValues
from surface.mesh.incidence import canonical_edge


def _directed(values, start, end):
    key = canonical_edge(start, end)
    value = values.edge_integrals[key]
    return value if (start, end) == key else -value


def transfer_fields(source_mesh, source_values, target_mesh, evidence):
    node_scalars = {}
    for node, item in evidence["nodes"].items():
        vertices = source_mesh.faces[item["source_face"]]
        node_scalars[node] = sum(
            (weight * source_values.node_scalars[vertex]
             for weight, vertex in zip(item["weights"], vertices)),
            Fraction(0))
    edge_integrals = {}
    for edge, pieces in evidence["edges"].items():
        total = Fraction(0)
        for piece in pieces:
            vertices = source_mesh.faces[piece["source_face"]]
            start, end = piece["start_weights"], piece["end_weights"]
            delta = [end[i] - start[i] for i in range(3)]
            mean = [(start[i] + end[i]) / 2 for i in range(3)]
            for i in range(3):
                for j in range(i + 1, 3):
                    directed = _directed(source_values, vertices[i], vertices[j])
                    total += directed * (mean[i] * delta[j] - mean[j] * delta[i])
        edge_integrals[edge] = total
    face_totals = {face: Fraction(0) for face in target_mesh.faces}
    for item in evidence["overlaps"]:
        face_totals[item["target_face"]] += (
            item["source_fraction"]
            * source_values.face_totals[item["source_face"]])
    return FieldValues(node_scalars, edge_integrals, face_totals)
