from dataclasses import dataclass
from surface.sources.revisions import RevisionRepository, ConflictError
from surface.mesh.barycentric import validate_sample
from surface.results.codec import encode_payload, decode_payload, canonical_digest

CONVENTION = "ordinary-surface-v1"


@dataclass(frozen=True)
class AnalysisPlan:
    plan_id: int
    request_id: str
    revision_id: int
    source_digest: str
    samples: tuple
    convention: str
    fingerprint: str

    def binding(self):
        return {"revision_id": self.revision_id, "source_digest": self.source_digest,
                "samples": [dict(item) for item in self.samples],
                "convention": self.convention}


class PlanService:
    def __init__(self, store):
        self.store = store
        self.revisions = RevisionRepository(store)

    @staticmethod
    def initialize(store):
        store.connection.execute(
            "CREATE TABLE IF NOT EXISTS analysis_plans (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE, revision_id INTEGER NOT NULL REFERENCES source_revisions(id), fingerprint TEXT NOT NULL, payload BLOB NOT NULL)")

    def create_plan(self, request_id, revision_id, samples):
        if type(request_id) is not str or not request_id:
            raise ValueError("nonempty plan request ID required")
        revision = self.revisions.read(revision_id)
        if type(samples) not in (list, tuple):
            raise ValueError("ordered sample sequence required")
        normalized = [validate_sample(revision.mesh, item) for item in samples]
        binding = {"revision_id": revision_id, "source_digest": revision.fingerprint,
                   "samples": normalized, "convention": CONVENTION}
        raw = encode_payload(binding)
        with self.store.transaction():
            row = self.store.connection.execute(
                "SELECT id FROM analysis_plans WHERE request_id=?", (request_id,)).fetchone()
            if row is not None:
                prior = self.read_plan(row[0])
                if encode_payload(prior.binding()) != raw:
                    raise ConflictError("plan request bound to different inputs")
                return prior.plan_id
            cursor = self.store.connection.execute(
                "INSERT INTO analysis_plans(request_id,revision_id,fingerprint,payload) VALUES(?,?,?,?)",
                (request_id, revision_id, canonical_digest(binding), raw))
            self.store.checkpoint("plan_after_insert")
            return cursor.lastrowid

    def read_plan(self, plan_id):
        if type(plan_id) is not int or plan_id < 1:
            raise KeyError(plan_id)
        row = self.store.connection.execute(
            "SELECT request_id,revision_id,fingerprint,payload FROM analysis_plans WHERE id=?",
            (plan_id,)).fetchone()
        if row is None:
            raise KeyError(plan_id)
        try:
            binding = decode_payload(row[3])
            if (type(binding) is not dict or set(binding) != {
                    "revision_id", "source_digest", "samples", "convention"}
                    or canonical_digest(binding) != row[2]
                    or binding["revision_id"] != row[1]
                    or type(binding["revision_id"]) is not int
                    or binding["convention"] != CONVENTION
                    or type(binding["source_digest"]) is not str
                    or type(binding["samples"]) is not list):
                raise ValueError("damaged or unsupported plan binding")
            return AnalysisPlan(plan_id, row[0], row[1], binding["source_digest"],
                                tuple(binding["samples"]), CONVENTION, row[2])
        except (KeyError, TypeError) as exc:
            raise ValueError("damaged analysis plan") from exc
