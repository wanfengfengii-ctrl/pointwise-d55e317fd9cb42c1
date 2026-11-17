from surface.sources.revisions import RevisionRepository
from surface.tasks.plans import PlanService, RETRIANGULATION_CONVENTION
from surface.tasks.inputs import resolve_inputs
from surface.tasks.receipts import ReceiptRepository
from surface.tasks.publication import publish_result
from surface.mesh.barycentric import validate_sample
from surface.mesh.overlay import build_transfer_evidence
from surface.fields.bindings import bind_fields
from surface.fields.sampling import sample_queue
from surface.fields.circulation import all_circulations
from surface.fields.balances import build_balance
from surface.fields.transfer import transfer_fields
from surface.results.repository import ResultRepository, ResultPayload


def compute_analysis(inputs, checkpoint=None):
    plan, revision, bundle = inputs.plan, inputs.revision, inputs.bundle
    samples = [sample.to_record() for sample in sample_queue(bundle, plan.samples)]
    if checkpoint is not None:
        checkpoint("compute_after_samples")
    circulations = all_circulations(bundle)
    balance = build_balance(bundle, circulations).to_record()
    return ResultPayload({
        "kind": "ordinary-analysis-v1",
        "binding": {"plan_id": plan.plan_id, "plan_digest": plan.fingerprint,
                    "revision_id": revision.revision_id,
                    "source_digest": revision.fingerprint, "convention": plan.convention},
        "input": {"mesh": revision.mesh.to_record(), "fields": revision.fields.to_record()},
        "requests": [dict(item) for item in plan.samples],
        "samples": samples, "circulations": circulations,
        "face_totals": dict(bundle.values.face_totals), "balance": balance}).to_record()


def compute_retriangulation(plan, revisions, checkpoint=None):
    revision = revisions.read(plan.revision_id)
    if revision.fingerprint != plan.source_digest:
        raise ValueError("plan source digest mismatch")
    target = plan.target_mesh
    for sample in plan.samples:
        validate_sample(target, sample)
    evidence = build_transfer_evidence(revision.mesh, target)
    values = transfer_fields(revision.mesh, revision.fields, target, evidence)
    bundle = bind_fields(revision.revision_id, target, values)
    samples = [sample.to_record() for sample in sample_queue(bundle, plan.samples)]
    if checkpoint is not None:
        checkpoint("compute_after_samples")
    circulations = all_circulations(bundle)
    balance = build_balance(bundle, circulations).to_record()
    return ResultPayload({
        "kind": "retriangulated-analysis-v1",
        "binding": {"plan_id": plan.plan_id, "plan_digest": plan.fingerprint,
                    "revision_id": revision.revision_id,
                    "source_digest": revision.fingerprint, "convention": plan.convention},
        "input": {"mesh": revision.mesh.to_record(), "fields": revision.fields.to_record()},
        "target": {"mesh": target.to_record(), "fields": values.to_record()},
        "requests": [dict(item) for item in plan.samples],
        "samples": samples, "circulations": circulations,
        "face_totals": dict(values.face_totals), "balance": balance,
        "transfer": evidence}).to_record()


class AnalysisService:
    def __init__(self, store):
        self.store = store
        self.plans = PlanService(store)
        self.revisions = RevisionRepository(store)
        self.receipts = ReceiptRepository(store)
        self.results = ResultRepository(store)

    def execute(self, plan_id):
        plan = self.plans.read_plan(plan_id)
        receipt = self.receipts.find(plan_id)
        if receipt is not None:
            result = self.results.read(receipt.result_id)
            if (result.payload.value["binding"]["plan_id"] != plan_id
                    or result.payload.value["binding"]["plan_digest"] != plan.fingerprint):
                raise ValueError("damaged completed plan")
            return receipt.result_id
        if plan.convention == RETRIANGULATION_CONVENTION:
            payload = compute_retriangulation(plan, self.revisions, self.store.checkpoint)
        else:
            inputs = resolve_inputs(plan, self.revisions)
            payload = compute_analysis(inputs, self.store.checkpoint)
        return publish_result(self.store, self.results, self.receipts, plan_id, payload)
