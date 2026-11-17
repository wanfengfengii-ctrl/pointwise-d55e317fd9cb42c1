from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from surface.results.codec import encode_payload, decode_payload, canonical_digest
from surface.mesh.model import OrdinaryMesh
from surface.sources.field_input import normalize_fields
from surface.mesh.barycentric import validate_sample
from surface.tasks.plans import CONVENTION, RETRIANGULATION_CONVENTION


def _fractions(mapping, identities):
    if (type(mapping) is not dict or set(mapping) != set(identities)
            or any(not isinstance(value, Fraction) for value in mapping.values())):
        raise ValueError("invalid complete rational mapping")


def _validate_requests_samples(mesh, requests, samples):
    if type(requests) is not list:
        raise ValueError("ordered requests required")
    normalized = [validate_sample(mesh, sample) for sample in requests]
    if requests != normalized:
        raise ValueError("noncanonical sample requests")
    if type(samples) is not list or len(samples) != len(requests):
        raise ValueError("incomplete samples")
    for request, sample in zip(requests, samples):
        if (type(sample) is not dict or set(sample) != {"face", "weights", "position", "value"}
                or sample["face"] != request["face"] or sample["weights"] != request["weights"]
                or type(sample["position"]) is not tuple or len(sample["position"]) != 3
                or any(not isinstance(x, Fraction) for x in sample["position"])
                or not isinstance(sample["value"], Fraction)):
            raise ValueError("invalid stored sample")


def _validate_balance(balance, mesh):
    if type(balance) is not dict or set(balance) != {
            "face_gaps", "boundary_terms", "internal_terms", "boundary_total",
            "internal_total", "circulation_total", "face_total", "gap_total"}:
        raise ValueError("incomplete accounting")
    _fractions(balance["face_gaps"], mesh.faces)
    for key in ("boundary_total", "internal_total", "circulation_total", "face_total", "gap_total"):
        if not isinstance(balance[key], Fraction):
            raise ValueError("nonrational balance")
    for name, degree in (("boundary_terms", 1), ("internal_terms", 2)):
        terms = balance[name]
        expected = {edge for edge, uses in mesh.incidence.items() if len(uses) == degree}
        if type(terms) is not list or len(terms) != len(expected):
            raise ValueError("incomplete edge ledger")
        seen = set()
        for item in terms:
            if (type(item) is not dict or set(item) != {"edge", "coefficient", "value"}
                    or type(item["edge"]) is not tuple or item["edge"] not in expected
                    or item["edge"] in seen or type(item["coefficient"]) is not int
                    or not isinstance(item["value"], Fraction)):
                raise ValueError("invalid ledger item")
            seen.add(item["edge"])


def _validate_binding(binding, source, convention):
    if (type(binding) is not dict or set(binding) != {
            "plan_id", "plan_digest", "revision_id", "source_digest", "convention"}
            or any(type(binding[k]) is not int or binding[k] < 1
                   for k in ("plan_id", "revision_id"))
            or binding["convention"] != convention
            or any(type(binding[k]) is not str for k in ("source_digest", "plan_digest"))
            or type(source) is not dict or set(source) != {"mesh", "fields"}
            or canonical_digest(source) != binding["source_digest"]):
        raise ValueError("invalid fixed source binding")


def _validate_ordinary(record):
    names = {"kind", "binding", "input", "requests", "samples", "circulations",
             "face_totals", "balance"}
    if set(record) != names:
        raise ValueError("invalid result structure")
    binding, source = record["binding"], record["input"]
    _validate_binding(binding, source, CONVENTION)
    mesh = OrdinaryMesh.from_record(source["mesh"])
    fields = normalize_fields(mesh, source["fields"])
    requests = record["requests"]
    _validate_requests_samples(mesh, requests, record["samples"])
    plan_binding = {"revision_id": binding["revision_id"],
                    "source_digest": binding["source_digest"],
                    "samples": requests, "convention": CONVENTION}
    if canonical_digest(plan_binding) != binding["plan_digest"]:
        raise ValueError("invalid fixed plan binding")
    _fractions(record["circulations"], mesh.faces)
    _fractions(record["face_totals"], mesh.faces)
    if record["face_totals"] != fields.to_record()["face_totals"]:
        raise ValueError("stored totals differ from fixed input")
    _validate_balance(record["balance"], mesh)


def _valid_weights(value):
    return (type(value) is tuple and len(value) == 3
            and all(isinstance(w, Fraction) for w in value)
            and all(w >= 0 for w in value) and sum(value) == 1)


def _validate_transfer(transfer, source_mesh, target_mesh):
    if type(transfer) is not dict or set(transfer) != {"nodes", "edges", "overlaps"}:
        raise ValueError("incomplete transfer evidence")
    nodes, edges, overlaps = transfer["nodes"], transfer["edges"], transfer["overlaps"]
    if type(nodes) is not dict or set(nodes) != set(target_mesh.nodes):
        raise ValueError("node evidence must cover target nodes")
    for item in nodes.values():
        if (type(item) is not dict or set(item) != {"source_face", "weights"}
                or item["source_face"] not in source_mesh.faces
                or not _valid_weights(item["weights"])):
            raise ValueError("invalid node evidence")
    if type(edges) is not dict or set(edges) != set(target_mesh.edges):
        raise ValueError("edge evidence must cover target edges")
    for edge, pieces in edges.items():
        if type(edge) is not tuple or type(pieces) is not list or not pieces:
            raise ValueError("invalid edge evidence")
        for item in pieces:
            if (type(item) is not dict or set(item) != {
                    "source_face", "interval", "start_weights", "end_weights"}
                    or item["source_face"] not in source_mesh.faces):
                raise ValueError("invalid edge evidence")
            interval = item["interval"]
            if (type(interval) is not tuple or len(interval) != 2
                    or any(not isinstance(t, Fraction) for t in interval)
                    or interval[0] < 0 or interval[0] >= interval[1]
                    or interval[1] > 1
                    or not _valid_weights(item["start_weights"])
                    or not _valid_weights(item["end_weights"])):
                raise ValueError("invalid edge evidence")
    if type(overlaps) is not list:
        raise ValueError("invalid overlap evidence")
    for item in overlaps:
        if (type(item) is not dict or set(item) != {
                "source_face", "target_face", "source_weights", "source_fraction"}
                or item["source_face"] not in source_mesh.faces
                or item["target_face"] not in target_mesh.faces
                or type(item["source_weights"]) is not tuple
                or len(item["source_weights"]) < 3
                or any(not _valid_weights(w) for w in item["source_weights"])
                or not isinstance(item["source_fraction"], Fraction)
                or item["source_fraction"] <= 0):
            raise ValueError("invalid overlap evidence")


def _validate_retriangulated(record):
    names = {"kind", "binding", "input", "requests", "samples", "circulations",
             "face_totals", "balance", "target", "transfer"}
    if set(record) != names:
        raise ValueError("invalid result structure")
    binding, source = record["binding"], record["input"]
    _validate_binding(binding, source, RETRIANGULATION_CONVENTION)
    source_mesh = OrdinaryMesh.from_record(source["mesh"])
    normalize_fields(source_mesh, source["fields"])
    target = record["target"]
    if type(target) is not dict or set(target) != {"mesh", "fields"}:
        raise ValueError("invalid target record")
    target_mesh = OrdinaryMesh.from_record(target["mesh"])
    target_fields = normalize_fields(target_mesh, target["fields"])
    requests = record["requests"]
    _validate_requests_samples(target_mesh, requests, record["samples"])
    plan_binding = {"revision_id": binding["revision_id"],
                    "source_digest": binding["source_digest"],
                    "samples": requests, "convention": RETRIANGULATION_CONVENTION,
                    "target_mesh": target["mesh"]}
    if canonical_digest(plan_binding) != binding["plan_digest"]:
        raise ValueError("invalid fixed plan binding")
    _fractions(record["circulations"], target_mesh.faces)
    _fractions(record["face_totals"], target_mesh.faces)
    if record["face_totals"] != target_fields.to_record()["face_totals"]:
        raise ValueError("stored totals differ from fixed input")
    _validate_balance(record["balance"], target_mesh)
    _validate_transfer(record["transfer"], source_mesh, target_mesh)


def _validate(record):
    if type(record) is not dict:
        raise ValueError("invalid result structure")
    kind = record.get("kind")
    if kind == "ordinary-analysis-v1":
        _validate_ordinary(record)
    elif kind == "retriangulated-analysis-v1":
        _validate_retriangulated(record)
    else:
        raise ValueError("unsupported result kind")


@dataclass(frozen=True)
class ResultPayload:
    value: dict

    def __post_init__(self):
        try:
            _validate(self.value)
        except (KeyError, TypeError, IndexError, AttributeError) as exc:
            raise ValueError("damaged result payload") from exc
        object.__setattr__(self, "value", deepcopy(self.value))

    def to_record(self):
        return deepcopy(self.value)


@dataclass(frozen=True)
class ResultRecord:
    result_id: int
    fingerprint: str
    payload: ResultPayload

    def to_record(self):
        return {"result_id": self.result_id, "fingerprint": self.fingerprint,
                "payload": self.payload.to_record()}


class ResultRepository:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def initialize(store):
        store.connection.execute(
            "CREATE TABLE IF NOT EXISTS analysis_results (id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER NOT NULL UNIQUE REFERENCES analysis_plans(id), fingerprint TEXT NOT NULL, payload BLOB NOT NULL)")

    def stage(self, plan_id, payload):
        from surface.tasks.plans import PlanService
        self.store.require_transaction()
        payload = ResultPayload(payload).to_record()
        plan = PlanService(self.store).read_plan(plan_id)
        expected = {"plan_id": plan_id, "plan_digest": plan.fingerprint,
                    "revision_id": plan.revision_id, "source_digest": plan.source_digest,
                    "convention": plan.convention}
        if payload["binding"] != expected or payload["requests"] != list(plan.samples):
            raise ValueError("result does not match publication plan")
        cursor = self.store.connection.execute(
            "INSERT INTO analysis_results(plan_id,fingerprint,payload) VALUES(?,?,?)",
            (plan_id, canonical_digest(payload), encode_payload(payload)))
        return cursor.lastrowid

    def read(self, result_id):
        if type(result_id) is not int or result_id < 1:
            raise KeyError(result_id)
        row = self.store.connection.execute(
            "SELECT plan_id,fingerprint,payload FROM analysis_results WHERE id=?",
            (result_id,)).fetchone()
        if row is None:
            raise KeyError(result_id)
        value = decode_payload(row[2])
        if canonical_digest(value) != row[1]:
            raise ValueError("result digest mismatch")
        payload = ResultPayload(value)
        if payload.value["binding"]["plan_id"] != row[0]:
            raise ValueError("result directory binding mismatch")
        return ResultRecord(result_id, row[1], payload)
