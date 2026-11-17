from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from surface.results.codec import encode_payload, decode_payload, canonical_digest
from surface.mesh.model import OrdinaryMesh
from surface.sources.field_input import normalize_fields
from surface.mesh.barycentric import validate_sample
from surface.tasks.plans import CONVENTION


def _fractions(mapping, identities):
    if (type(mapping) is not dict or set(mapping) != set(identities)
            or any(not isinstance(value, Fraction) for value in mapping.values())):
        raise ValueError("invalid complete rational mapping")


def _validate(record):
    names = {"kind", "binding", "input", "requests", "samples", "circulations",
             "face_totals", "balance"}
    if type(record) is not dict or set(record) != names:
        raise ValueError("invalid result structure")
    if record["kind"] != "ordinary-analysis-v1":
        raise ValueError("unsupported result kind")
    binding, source = record["binding"], record["input"]
    if (type(binding) is not dict or set(binding) != {
            "plan_id", "plan_digest", "revision_id", "source_digest", "convention"}
            or any(type(binding[k]) is not int or binding[k] < 1
                   for k in ("plan_id", "revision_id"))
            or binding["convention"] != CONVENTION
            or any(type(binding[k]) is not str for k in ("source_digest", "plan_digest"))
            or type(source) is not dict or set(source) != {"mesh", "fields"}
            or canonical_digest(source) != binding["source_digest"]):
        raise ValueError("invalid fixed source binding")
    mesh = OrdinaryMesh.from_record(source["mesh"])
    fields = normalize_fields(mesh, source["fields"])
    requests = record["requests"]
    if type(requests) is not list:
        raise ValueError("ordered requests required")
    normalized = [validate_sample(mesh, sample) for sample in requests]
    if requests != normalized:
        raise ValueError("noncanonical sample requests")
    plan_binding = {"revision_id": binding["revision_id"],
                    "source_digest": binding["source_digest"],
                    "samples": requests, "convention": CONVENTION}
    if canonical_digest(plan_binding) != binding["plan_digest"]:
        raise ValueError("invalid fixed plan binding")
    samples = record["samples"]
    if type(samples) is not list or len(samples) != len(requests):
        raise ValueError("incomplete samples")
    for request, sample in zip(requests, samples):
        if (type(sample) is not dict or set(sample) != {"face", "weights", "position", "value"}
                or sample["face"] != request["face"] or sample["weights"] != request["weights"]
                or type(sample["position"]) is not tuple or len(sample["position"]) != 3
                or any(not isinstance(x, Fraction) for x in sample["position"])
                or not isinstance(sample["value"], Fraction)):
            raise ValueError("invalid stored sample")
    _fractions(record["circulations"], mesh.faces)
    _fractions(record["face_totals"], mesh.faces)
    if record["face_totals"] != fields.to_record()["face_totals"]:
        raise ValueError("stored totals differ from fixed input")
    balance = record["balance"]
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
