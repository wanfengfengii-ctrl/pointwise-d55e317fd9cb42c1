from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class FaceEdge:
    edge: tuple
    sign: int


@dataclass(frozen=True)
class OrdinaryMesh:
    nodes: object
    faces: object
    face_edges: object
    incidence: object

    def __post_init__(self):
        for name in ("nodes", "faces", "face_edges", "incidence"):
            object.__setattr__(self, name, MappingProxyType(
                {key: tuple(value) for key, value in getattr(self, name).items()}))

    @property
    def edges(self):
        return tuple(sorted(self.incidence))

    def to_record(self):
        return {"nodes": dict(self.nodes), "faces": dict(self.faces),
                "edges": list(self.edges),
                "face_edges": {face: [{"edge": term.edge, "sign": term.sign}
                                     for term in self.face_edges[face]]
                               for face in sorted(self.faces)}}

    @classmethod
    def from_record(cls, record):
        from surface.sources.mesh_input import normalize_mesh
        if type(record) is not dict or set(record) != {
                "nodes", "faces", "edges", "face_edges"}:
            raise ValueError("invalid ordinary mesh record")
        mesh = normalize_mesh({"nodes": record["nodes"], "faces": record["faces"]})
        if mesh.to_record() != record:
            raise ValueError("inconsistent ordinary incidence")
        return mesh
