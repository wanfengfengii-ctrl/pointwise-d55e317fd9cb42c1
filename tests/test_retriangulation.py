"""Independent small-example audits for the retriangulation chain.

Hand-computed fixtures complement retriangulation_cases.py: source-vertex
crossing, legal collinear boundary splits, mixed conventions in one archive,
and rejection of T-junctions, holes, point contacts, coincident identities
and unused nodes.  No second general intersector is asserted here; every
expected value is a hand fraction.
"""
import unittest
from fractions import Fraction as F

from tests.test_sources import state
from surface.sources.storage import ArchiveStore
from surface.sources.catalog import SourceCatalog
from surface.tasks.plans import PlanService
from surface.tasks.execution import AnalysisService
from surface.results.reading import ResultReader
from surface.results.exporting import ResultExporter
from surface.results.checking import check_export


def square_source(edge_integrals=None, face_totals=None):
    nodes = {name: (F(x), F(y), F(0)) for name, (x, y) in {
        "A": (0, 0), "B": (1, 0), "C": (1, 1), "D": (0, 1)}.items()}
    mesh = {"nodes": nodes, "faces": {"s1": ("A", "B", "C"), "s2": ("A", "C", "D")}}
    edges = [("A", "B"), ("A", "C"), ("A", "D"), ("B", "C"), ("C", "D")]
    fields = {"node_scalars": {n: F(0) for n in nodes},
              "edge_integrals": {e: F(0) for e in edges},
              "face_totals": {"s1": F(0), "s2": F(0)}}
    fields["edge_integrals"].update(edge_integrals or {})
    fields["face_totals"].update(face_totals or {})
    return mesh, fields


class TestRetriangulationGeometry(unittest.TestCase):
    def setUp(self):
        self.store = ArchiveStore.open()
        self.addCleanup(self.store.close)
        self.catalog = SourceCatalog(self.store)
        self.plans = PlanService(self.store)

    def _run(self, source, fields, target, samples, request="case"):
        revision = self.catalog.put_source("source-" + request, source, fields)["revision_id"]
        plan = self.plans.create_retriangulation_plan(request, revision, target, samples)
        result_id = AnalysisService(self.store).execute(plan)
        return result_id, ResultReader(self.store).read_result(result_id)["payload"]

    def test_source_vertex_crossing(self):
        # Unit square source with center vertex M; the target diagonal AC
        # passes exactly through source vertex M and must be segmented there.
        nodes = {n: (F(x), F(y), F(0)) for n, (x, y) in {
            "A": (0, 0), "B": (1, 0), "C": (1, 1), "D": (0, 1)}.items()}
        nodes["M"] = (F(1, 2), F(1, 2), F(0))
        source = {"nodes": nodes, "faces": {
            "s1": ("A", "B", "M"), "s2": ("B", "C", "M"),
            "s3": ("C", "D", "M"), "s4": ("D", "A", "M")}}
        edges = [("A", "B"), ("A", "D"), ("A", "M"), ("B", "C"), ("B", "M"),
                 ("C", "D"), ("C", "M"), ("D", "M")]
        fields = {"node_scalars": {n: F(0) for n in nodes},
                  "edge_integrals": {e: F(0) for e in edges},
                  "face_totals": {f: F(0) for f in source["faces"]}}
        fields["edge_integrals"][("A", "M")] = F(1)
        target = {"nodes": {k: v for k, v in nodes.items() if k != "M"},
                  "faces": {"t1": ("A", "B", "C"), "t2": ("A", "C", "D")}}
        _, payload = self._run(source, fields, target, [], "crossing")
        transferred = payload["target"]["fields"]["edge_integrals"]
        self.assertEqual(transferred[("A", "C")], F(1))
        self.assertEqual(sum(transferred.values()), F(1))
        pieces = payload["transfer"]["edges"][("A", "C")]
        self.assertEqual([p["source_face"] for p in pieces], ["s1", "s2"])
        self.assertEqual([p["interval"] for p in pieces],
                         [(F(0), F(1, 2)), (F(1, 2), F(1))])
        self.assertEqual(payload["circulations"], {"t1": F(-1), "t2": F(1)})
        self.assertEqual(payload["balance"]["circulation_total"], F(0))

    def test_boundary_collinear_split_and_overlap_handcheck(self):
        # Target legally inserts collinear boundary midpoint M on edge AB.
        source, fields = square_source({("A", "B"): F(1)},
                                       {"s1": F(2), "s2": F(6)})
        target = {"nodes": dict(source["nodes"]),
                  "faces": {"t1": ("A", "M", "D"), "t2": ("M", "B", "D"),
                            "t3": ("B", "C", "D")}}
        target["nodes"]["M"] = (F(1, 2), F(0), F(0))
        result_id, payload = self._run(source, fields, target, [], "split")
        transferred = payload["target"]["fields"]["edge_integrals"]
        # Hand fractions: AM and MB each carry half of the source AB integral
        # (canonical ("B","M") is directed against the source edge), and the
        # internal edge DM picks up 1/3 from its s1 segment.
        self.assertEqual(transferred[("A", "M")], F(1, 2))
        self.assertEqual(transferred[("B", "M")], F(-1, 2))
        self.assertEqual(transferred[("D", "M")], F(1, 3))
        self.assertEqual(payload["face_totals"],
                         {"t1": F(7, 3), "t2": F(5, 3), "t3": F(4)})
        self.assertEqual(len(payload["transfer"]["overlaps"]), 6)
        self.assertEqual(payload["balance"]["circulation_total"], F(1))
        report = check_export(ResultExporter(ResultReader(self.store))
                              .export_result(result_id))
        self.assertEqual(report.boundary_total, F(1))
        self.assertEqual(report.internal_total, F(0))
        self.assertEqual(report.gap_total, F(1) - F(8))

    def test_conventions_coexist_in_one_archive(self):
        source, fields = square_source({("A", "B"): F(1)},
                                       {"s1": F(2), "s2": F(6)})
        target = {"nodes": dict(source["nodes"]),
                  "faces": {"t1": ("A", "B", "D"), "t2": ("B", "C", "D")}}
        revision = self.catalog.put_source("mixed", source, fields)["revision_id"]
        old_plan = self.plans.create_plan("ordinary", revision, [])
        new_plan = self.plans.create_retriangulation_plan("retri", revision, target, [])
        analysis = AnalysisService(self.store)
        old_id, new_id = analysis.execute(old_plan), analysis.execute(new_plan)
        reader = ResultReader(self.store)
        old_payload = reader.read_result(old_id)["payload"]
        new_payload = reader.read_result(new_id)["payload"]
        self.assertEqual(old_payload["kind"], "ordinary-analysis-v1")
        self.assertEqual(new_payload["kind"], "retriangulated-analysis-v1")
        self.assertEqual(old_payload["balance"]["circulation_total"], F(1))
        self.assertEqual(new_payload["balance"]["circulation_total"], F(1))
        exporter = ResultExporter(reader)
        self.assertEqual(check_export(exporter.export_result(old_id)).gap_total, F(-7))
        self.assertEqual(check_export(exporter.export_result(new_id)).gap_total, F(-7))

    def test_reject_t_junction_even_when_region_matches(self):
        # Source and target triangulate the same union (triangle ABC plus the
        # downward triangle MDB), but the target keeps AB as one edge while a
        # second face attaches at its midpoint M: a T-junction.
        nodes = {n: (F(x), F(y), F(0)) for n, (x, y) in {
            "A": (0, 0), "B": (2, 0), "C": (0, 2), "M": (1, 0),
            "D": (2, -1)}.items()}
        source = {"nodes": nodes, "faces": {
            "s1": ("A", "M", "C"), "s2": ("M", "B", "C"), "s3": ("M", "D", "B")}}
        edges = [("A", "C"), ("A", "M"), ("B", "C"), ("B", "D"), ("B", "M"),
                 ("C", "M"), ("D", "M")]
        fields = {"node_scalars": {n: F(0) for n in nodes},
                  "edge_integrals": {e: F(0) for e in edges},
                  "face_totals": {f: F(0) for f in source["faces"]}}
        target = {"nodes": nodes, "faces": {"t1": ("A", "B", "C"),
                                            "t2": ("M", "D", "B")}}
        revision = self.catalog.put_source("tee", source, fields)["revision_id"]
        before = state(self.store)
        with self.assertRaises(ValueError):
            self.plans.create_retriangulation_plan("tee", revision, target, [])
        self.assertEqual(state(self.store), before)

    def test_reject_hole_point_contact_coincident_and_unused(self):
        source, fields = square_source()
        revision = self.catalog.put_source("reject", source, fields)["revision_id"]
        outer = {n: (F(x), F(y), F(0)) for n, (x, y) in {
            "E": (0, 0), "F": (3, 0), "G": (3, 3), "H": (0, 3)}.items()}
        frame_nodes = dict(outer)
        frame_nodes.update({n: (F(x), F(y), F(0)) for n, (x, y) in {
            "I": (1, 1), "J": (2, 1), "K": (2, 2), "L": (1, 2)}.items()})
        hole = {"nodes": frame_nodes, "faces": {
            "f1": ("E", "F", "J"), "f2": ("E", "J", "I"), "f3": ("F", "G", "K"),
            "f4": ("F", "K", "J"), "f5": ("G", "H", "L"), "f6": ("G", "L", "K"),
            "f7": ("H", "E", "I"), "f8": ("H", "I", "L")}}
        point_contact = {"nodes": {
            "A": (F(0), F(0), F(0)), "M": (F(1), F(0), F(0)),
            "B": (F(2), F(0), F(0)), "C": (F(0), F(2), F(0)),
            "X": (F(1), F(-1), F(0))},
            "faces": {"t1": ("A", "M", "C"), "t2": ("M", "X", "B")}}
        coincident = {"nodes": {
            "A": (F(0), F(0), F(0)), "B": (F(1), F(0), F(0)),
            "C": (F(1), F(1), F(0)), "D": (F(0), F(1), F(0)),
            "A2": (F(0), F(0), F(0))},
            "faces": {"t1": ("A", "B", "C"), "t2": ("A2", "C", "D")}}
        unused = {"nodes": dict(source["nodes"]), "faces": dict(source["faces"])}
        unused["nodes"]["Z"] = (F(1, 2), F(1, 2), F(0))
        before = state(self.store)
        for bad in (hole, point_contact, coincident, unused):
            with self.subTest(bad=sorted(bad["faces"])):
                with self.assertRaises(ValueError):
                    self.plans.create_retriangulation_plan("bad", revision, bad, [])
                self.assertEqual(state(self.store), before)

    def test_reads_and_exports_never_recompute(self):
        source, fields = square_source({("A", "B"): F(1)})
        target = {"nodes": dict(source["nodes"]),
                  "faces": {"t1": ("A", "B", "D"), "t2": ("B", "C", "D")}}
        result_id, payload = self._run(source, fields, target, [], "frozen")
        reader = ResultReader(self.store)
        original = reader.read_result(result_id)
        def fail(stage):
            if stage == "compute_after_samples":
                raise AssertionError("delivery must not recompute")
        self.store.fault = fail
        try:
            self.assertEqual(reader.read_result(result_id), original)
            raw = ResultExporter(reader).export_result(result_id)
        finally:
            self.store.fault = None
        self.assertEqual(check_export(raw).faces, 2)


if __name__ == "__main__":
    unittest.main()
