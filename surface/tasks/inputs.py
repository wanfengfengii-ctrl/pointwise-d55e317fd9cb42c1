from dataclasses import dataclass
from surface.fields.bindings import bind_fields
from surface.mesh.barycentric import validate_sample


@dataclass(frozen=True)
class ResolvedInputs:
    plan: object
    revision: object
    bundle: object


def resolve_inputs(plan, revisions):
    revision = revisions.read(plan.revision_id)
    if revision.fingerprint != plan.source_digest:
        raise ValueError("plan source digest mismatch")
    for sample in plan.samples:
        validate_sample(revision.mesh, sample)
    bundle = bind_fields(plan.revision_id, revision.mesh, revision.fields)
    return ResolvedInputs(plan, revision, bundle)
