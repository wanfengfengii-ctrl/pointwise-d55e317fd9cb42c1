from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType
from surface.mesh.incidence import canonical_edge


def validate_coverage(mesh, values):
    expected = (set(mesh.nodes), set(mesh.edges), set(mesh.faces))
    actual = (set(values.node_scalars), set(values.edge_integrals),
              set(values.face_totals))
    if expected != actual:
        raise ValueError("field identities must exactly cover the ordinary mesh")
    for mapping in (values.node_scalars, values.edge_integrals, values.face_totals):
        if any(not isinstance(value, Fraction) for value in mapping.values()):
            raise ValueError("field values must be Fractions")


@dataclass(frozen=True)
class FieldValues:
    node_scalars: object
    edge_integrals: object
    face_totals: object

    def __post_init__(self):
        for name in ("node_scalars", "edge_integrals", "face_totals"):
            object.__setattr__(self, name, MappingProxyType(
                dict(sorted(getattr(self, name).items()))))

    def to_record(self):
        return {name: dict(getattr(self, name))
                for name in ("node_scalars", "edge_integrals", "face_totals")}


@dataclass(frozen=True)
class FieldBundle:
    revision_id: int
    mesh: object
    values: FieldValues

    def edge_value(self, start, end):
        key = canonical_edge(start, end)
        value = self.values.edge_integrals[key]
        return value if (start, end) == key else -value


def bind_fields(revision_id, mesh, values):
    if type(revision_id) is not int or revision_id < 1:
        raise ValueError("invalid revision ID")
    validate_coverage(mesh, values)
    return FieldBundle(revision_id, mesh, values)
