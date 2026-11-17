import copy
import json
import unittest
from fractions import Fraction as F
from tests.test_sources import fixture
from surface.sources.storage import ArchiveStore
from surface.sources.catalog import SourceCatalog
from surface.sources.revisions import RevisionRepository
from surface.fields.bindings import bind_fields
from surface.mesh.boundary import boundary_terms, internal_terms, summed_face_chain
from surface.tasks.plans import PlanService
from surface.tasks.execution import AnalysisService
from surface.results.reading import ResultReader
from surface.results.checking import check_result_payload


class TestAnalysis(unittest.TestCase):
    def setUp(self):
        self.store = ArchiveStore.open()
        self.addCleanup(self.store.close)
        self.catalog = SourceCatalog(self.store)
        self.plans = PlanService(self.store)
        self.analysis = AnalysisService(self.store)
        self.reader = ResultReader(self.store)

    def test_sampling_and_circulation(self):
        mesh, fields, samples = fixture()
        first = self.catalog.put_source("solver", mesh, fields)
        source_before = self.catalog.read_source(first["revision_id"])
        plan = self.plans.create_plan("qc", first["revision_id"], samples)
        submitted = copy.deepcopy(samples)
        samples[0]["weights"] = (F(0), F(1), F(0))
        samples.reverse()
        changed = copy.deepcopy(fields)
        changed["node_scalars"] = {k: value + F(100) for k, value in fields["node_scalars"].items()}
        changed["edge_integrals"] = {k: value + F(10) for k, value in fields["edge_integrals"].items()}
        changed["face_totals"] = {"f1": F(40), "f2": F(50)}
        second = self.catalog.put_source("corrected", mesh, changed)
        self.assertNotEqual(first["revision_id"], second["revision_id"])
        result_id = self.analysis.execute(plan)
        payload = self.reader.read_result(result_id)["payload"]
        self.assertEqual(payload["binding"]["revision_id"], first["revision_id"])
        self.assertEqual(payload["input"], {"mesh": source_before["mesh"], "fields": fields})
        self.assertEqual(payload["requests"], submitted)
        self.assertEqual([s["value"] for s in payload["samples"]], [F(0), F(1), F(4, 3), F(5), F(4, 3)])
        self.assertEqual([s["position"] for s in payload["samples"]],
                         [(F(0), F(0), F(0)), (F(1, 2), F(0), F(0)),
                          (F(1, 3), F(1, 6), F(0)), (F(1, 2), F(1), F(0)),
                          (F(1, 3), F(1, 6), F(0))])
        self.assertEqual(payload["circulations"], {"f1": F(62, 105), "f2": F(37, 70)})
        balance = payload["balance"]
        self.assertEqual(balance["boundary_total"], F(47, 42))
        self.assertEqual(balance["internal_total"], F(0))
        self.assertEqual(balance["internal_terms"],
                         [{"edge": ("B", "C"), "coefficient": 0, "value": F(0)}])
        self.assertEqual(balance["face_gaps"], {"f1": F(62, 105), "f2": F(251, 210)})
        self.assertEqual(balance["gap_total"], F(25, 14))
        revision = RevisionRepository(self.store).read(first["revision_id"])
        bundle = bind_fields(first["revision_id"], revision.mesh, revision.fields)
        self.assertEqual(bundle.edge_value("C", "B"), F(-2, 5))
        self.assertEqual(boundary_terms(revision.mesh),
                         {("A", "B"): 1, ("A", "C"): -1, ("B", "D"): 1, ("C", "D"): -1})
        self.assertEqual(internal_terms(revision.mesh), {("B", "C"): 0})
        self.assertEqual(summed_face_chain(revision.mesh)[("B", "C")], 0)
        self.assertEqual(check_result_payload(payload).boundary_total, F(47, 42))
        same_direction = copy.deepcopy(mesh)
        same_direction["faces"]["f2"] = ("B", "C", "D")
        r = self.catalog.put_source("same-direction", same_direction, fields)
        p = self.plans.create_plan("same-direction", r["revision_id"], [])
        other = self.reader.read_result(self.analysis.execute(p))["payload"]
        self.assertEqual(other["circulations"], {"f1": F(62, 105), "f2": F(-37, 70)})
        self.assertEqual(other["balance"]["internal_total"], F(4, 5))
        self.assertEqual(other["balance"]["boundary_total"], F(-31, 42))
        self.assertEqual(other["balance"]["circulation_total"], F(13, 210))
        self.assertEqual(other["balance"]["internal_terms"],
                         [{"edge": ("B", "C"), "coefficient": 2, "value": F(4, 5)}])
        check_result_payload(other)
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "v2.boundary_ledger",
            "input": {"faces": {"f1": ["A", "B", "C"], "f2": ["B", "D", "C"]}},
            "expected": {"f1": "62/105", "f2": "37/70"},
            "actual": {k: str(v) for k, v in payload["circulations"].items()},
            "downstream_expected": {"boundary": "47/42", "internal": "0", "gap": "25/14"},
            "downstream_actual": {"boundary": str(balance["boundary_total"]),
                                  "internal": str(balance["internal_total"]),
                                  "gap": str(balance["gap_total"])},
            "code_paths": ["surface/mesh/incidence.py", "surface/tasks/inputs.py",
                           "surface/fields/circulation.py", "surface/fields/balances.py"],
            "oracle": "hand sums 1/3+2/5-1/7 and 3/7+1/2-2/5"
        }, sort_keys=True))

    def test_empty_samples_and_signed_totals(self):
        mesh, fields, _ = fixture()
        revision = self.catalog.put_source("signed", mesh, fields)
        plan = self.plans.create_plan("empty", revision["revision_id"], [])
        result = self.reader.read_result(self.analysis.execute(plan))["payload"]
        self.assertEqual(result["samples"], [])
        self.assertEqual(result["requests"], [])
        self.assertEqual(result["face_totals"], {"f1": F(0), "f2": F(-2, 3)})
        self.assertEqual(result["circulations"], {"f1": F(62, 105), "f2": F(37, 70)})
        self.assertEqual(result["balance"]["face_total"], F(-2, 3))
        self.assertEqual(result["balance"]["gap_total"], F(25, 14))
        self.assertEqual(result["balance"]["face_gaps"], {"f1": F(62, 105), "f2": F(251, 210)})
        check_result_payload(result)


if __name__ == "__main__":
    unittest.main()
