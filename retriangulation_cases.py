"""Finite public inputs and hand checks; no overlay or transfer implementation."""
import argparse
import copy
import json
import sqlite3
import tempfile
import unittest
from fractions import Fraction as F
from pathlib import Path

from surface.sources.storage import ArchiveStore
from surface.sources.catalog import SourceCatalog
from surface.sources.mesh_input import normalize_mesh
from surface.sources.field_input import normalize_fields
from surface.sources.revisions import ConflictError
from surface.tasks.plans import PlanService
from surface.tasks.execution import AnalysisService
from surface.results.reading import ResultReader
from surface.results.exporting import ResultExporter
from surface.results.codec import decode_payload, canonical_digest


def diagonal_case():
    nodes = {name: tuple(F(x) for x in xyz) for name, xyz in {
        "A": (0, 0, 0), "B": (1, 0, 0),
        "C": (1, 1, 0), "D": (0, 1, 0)}.items()}
    source = {"nodes": nodes, "faces": {
        "s1": ("A", "B", "C"), "s2": ("A", "C", "D")}}
    fields = {"node_scalars": {n: F(0) for n in nodes},
              "edge_integrals": {("A", "B"): F(1), ("A", "C"): F(0),
                                 ("A", "D"): F(0), ("B", "C"): F(0),
                                 ("C", "D"): F(0)},
              "face_totals": {"s1": F(2), "s2": F(6)}}
    target = {"nodes": dict(nodes), "faces": {
        "t1": ("A", "B", "D"), "t2": ("B", "C", "D")}}
    samples = [{"face": "t1", "weights": (F(1, 3),) * 3},
               {"face": "t2", "weights": (F(0), F(1), F(0))},
               {"face": "t1", "weights": (F(1, 3),) * 3}]
    return source, fields, target, samples


def fan_case():
    source, fields, target, _ = diagonal_case()
    fields["node_scalars"] = {"A": F(0), "B": F(2), "C": F(4), "D": F(8)}
    target["nodes"]["O"] = (F(1, 3), F(1, 2), F(0))
    target["faces"] = {"t1": ("A", "B", "O"), "t2": ("B", "C", "O"),
                       "t3": ("C", "D", "O"), "t4": ("D", "A", "O")}
    return source, fields, target, [
        {"face": "t1", "weights": (F(1, 3),) * 3},
        {"face": "t4", "weights": (F(0), F(0), F(1))}]


def concave_case():
    nodes = {n: (F(x), F(y), F(0)) for n, (x, y) in {
        "A": (0, 0), "B": (2, 0), "C": (2, 1),
        "D": (1, 1), "E": (1, 2), "F": (0, 2)}.items()}
    source = {"nodes": nodes, "faces": {
        "a": ("A", "B", "D"), "b": ("B", "C", "D"),
        "c": ("A", "D", "E"), "d": ("A", "E", "F")}}
    mesh = normalize_mesh(source)
    fields = {"node_scalars": {n: F(0) for n in nodes},
              "edge_integrals": {e: F(0) for e in mesh.edges},
              "face_totals": dict(zip(sorted(mesh.faces), map(F, (1, -2, 3, 4))))}
    return source, fields, copy.deepcopy(source), []


def vertical_renamed_case():
    source, fields, target, samples = diagonal_case()
    names = {"A": "m", "B": "z", "C": "n", "D": "a"}
    def mesh_change(mesh):
        return {"nodes": {names[n]: (F(5), xyz[0], xyz[1])
                          for n, xyz in mesh["nodes"].items()},
                "faces": {f: tuple(names[n] for n in vs)
                          for f, vs in mesh["faces"].items()}}
    fields["node_scalars"] = {names[n]: v for n, v in fields["node_scalars"].items()}
    edges = {}
    for (a, b), value in fields["edge_integrals"].items():
        u, v = names[a], names[b]
        edges[tuple(sorted((u, v)))] = value if u < v else -value
    fields["edge_integrals"] = edges
    return mesh_change(source), fields, mesh_change(target), samples


def state(store):
    tables = [r[0] for r in store.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {t: store.connection.execute('SELECT * FROM "' + t + '" ORDER BY 1').fetchall()
            for t in sorted(tables)}


def make_result(store, case, request="new-plan"):
    source, fields, target, samples = case
    revision = SourceCatalog(store).put_source("source-" + request, source, fields)
    plan = PlanService(store).create_retriangulation_plan(
        request, revision["revision_id"], target, samples)
    result_id = AnalysisService(store).execute(plan)
    return revision, plan, result_id, ResultReader(store).read_result(result_id)


def check_geometry_evidence(test, payload):
    """Check saved source parameters, not a reference geometric constructor."""
    source = payload["input"]["mesh"]
    target = payload["target"]["mesh"]
    evidence = payload["transfer"]
    test.assertEqual(set(evidence), {"nodes", "edges", "overlaps"})
    test.assertEqual(set(evidence["nodes"]), set(target["nodes"]))
    test.assertEqual(set(evidence["edges"]), set(target["edges"]))
    def position(face, weights):
        test.assertIsInstance(weights, tuple)
        test.assertEqual(len(weights), 3)
        test.assertTrue(all(isinstance(w, F) and w >= 0 for w in weights))
        test.assertEqual(sum(weights), 1)
        vertices = source["faces"][face]
        return tuple(sum((weights[i] * source["nodes"][vertices[i]][a]
                          for i in range(3)), F(0)) for a in range(3))
    for n, item in evidence["nodes"].items():
        test.assertEqual(set(item), {"source_face", "weights"})
        test.assertEqual(position(item["source_face"], item["weights"]), target["nodes"][n])
    for edge, pieces in evidence["edges"].items():
        test.assertIsInstance(pieces, list)
        test.assertTrue(pieces)
        previous = F(0)
        for item in pieces:
            test.assertEqual(set(item), {
                "source_face", "interval", "start_weights", "end_weights"})
            t0, t1 = item["interval"]
            test.assertTrue(isinstance(t0, F) and isinstance(t1, F))
            test.assertEqual(t0, previous)
            test.assertTrue(t0 < t1 <= 1)
            for t, name in ((t0, "start_weights"), (t1, "end_weights")):
                xyz = tuple((1-t)*target["nodes"][edge[0]][a] +
                            t*target["nodes"][edge[1]][a] for a in range(3))
                test.assertEqual(position(item["source_face"], item[name]), xyz)
            previous = t1
        test.assertEqual(previous, 1)
    seen = set()
    fractions = {f: F(0) for f in source["faces"]}
    for item in evidence["overlaps"]:
        test.assertEqual(set(item), {
            "source_face", "target_face", "source_weights", "source_fraction"})
        key = item["source_face"], item["target_face"]
        test.assertNotIn(key, seen)
        seen.add(key)
        test.assertIn(key[1], target["faces"])
        vertices = item["source_weights"]
        test.assertIsInstance(vertices, tuple)
        test.assertGreaterEqual(len(vertices), 3)
        test.assertNotEqual(vertices[0], vertices[-1])
        for weights in vertices:
            position(key[0], weights)
        test.assertIsInstance(item["source_fraction"], F)
        test.assertGreater(item["source_fraction"], 0)
        fractions[key[0]] += item["source_fraction"]
    test.assertEqual(fractions, {f: F(1) for f in source["faces"]})


class RetriangulationCases(unittest.TestCase):
    def setUp(self):
        self.store = ArchiveStore.open()
        self.addCleanup(self.store.close)

    def test_diagonal_and_saved_evidence(self):
        _, plan, result_id, result = make_result(self.store, diagonal_case())
        p = result["payload"]
        self.assertEqual(p["kind"], "retriangulated-analysis-v1")
        self.assertEqual(p["binding"]["convention"], "retriangulated-whitney-v1")
        self.assertEqual(p["binding"]["source_digest"], canonical_digest(p["input"]))
        self.assertEqual(p["target"]["fields"]["edge_integrals"], {
            ("A", "B"): F(1), ("A", "D"): F(0), ("B", "C"): F(0),
            ("B", "D"): F(-1, 2), ("C", "D"): F(0)})
        self.assertEqual(p["target"]["fields"]["node_scalars"],
                         {n: F(0) for n in "ABCD"})
        self.assertEqual(p["face_totals"], {"t1": F(4), "t2": F(4)})
        self.assertEqual(p["circulations"], {"t1": F(1, 2), "t2": F(1, 2)})
        self.assertEqual([s["value"] for s in p["samples"]], [F(0)] * 3)
        self.assertEqual(p["balance"]["circulation_total"], F(1))
        check_geometry_evidence(self, p)
        self.store.fault = lambda stage: (_ for _ in ()).throw(
            AssertionError("completed plan must not recompute")) if stage == "compute_after_samples" else None
        self.assertEqual(AnalysisService(self.store).execute(plan), result_id)

    def test_nonuniform_fan(self):
        _, _, _, result = make_result(self.store, fan_case())
        p = result["payload"]
        self.assertEqual(p["target"]["fields"]["node_scalars"]["O"], F(8, 3))
        self.assertEqual(p["target"]["fields"]["edge_integrals"][("B", "O")], F(-4, 7))
        self.assertEqual(p["face_totals"],
                         {"t1": F(9, 7), "t2": F(12, 7), "t3": F(3), "t4": F(2)})
        self.assertEqual(p["circulations"],
                         {"t1": F(3, 7), "t2": F(4, 7), "t3": F(0), "t4": F(0)})
        self.assertEqual([s["value"] for s in p["samples"]], [F(14, 9), F(8, 3)])
        self.assertEqual(p["balance"]["face_total"], F(8))
        check_geometry_evidence(self, p)

    def test_identity_shared_edge_and_signed_totals(self):
        source, fields, _, _ = diagonal_case()
        fields["edge_integrals"][("A", "C")] = F(7, 3)
        fields["face_totals"] = {"s1": F(0), "s2": F(-6)}
        _, _, _, result = make_result(
            self.store, (source, fields, copy.deepcopy(source), []))
        p = result["payload"]
        self.assertEqual(p["target"]["fields"], fields)
        self.assertEqual(p["samples"], [])
        check_geometry_evidence(self, p)
        pieces = p["transfer"]["edges"][("A", "C")]
        self.assertEqual(sum((b-a for a, b in (x["interval"] for x in pieces)), F(0)), 1)

    def test_vertical_direction_and_concavity(self):
        _, _, _, result = make_result(self.store, vertical_renamed_case(), "vertical")
        p = result["payload"]
        self.assertEqual(p["target"]["fields"]["edge_integrals"][("a", "z")], F(1, 2))
        self.assertEqual(p["face_totals"], {"t1": F(4), "t2": F(4)})
        check_geometry_evidence(self, p)
        source, fields, target, samples = concave_case()
        _, _, _, result = make_result(
            self.store, (source, fields, target, samples), "concave")
        self.assertEqual(result["payload"]["target"]["fields"], fields)
        check_geometry_evidence(self, result["payload"])

    def test_reject_geometry_without_binding(self):
        source, fields, target, samples = diagonal_case()
        revision = SourceCatalog(self.store).put_source("source", source, fields)["revision_id"]
        invalid = []
        moved = copy.deepcopy(target)
        moved["nodes"] = {n: (x+F(1, 2), y, z) for n, (x, y, z) in moved["nodes"].items()}
        invalid.append(moved)
        missing = copy.deepcopy(target)
        missing["faces"] = {"t1": missing["faces"]["t1"]}
        del missing["nodes"]["C"]
        invalid.append(missing)
        overlap = copy.deepcopy(target)
        overlap["faces"]["t2"] = overlap["faces"]["t1"]
        invalid.append(overlap)
        reverse = copy.deepcopy(target)
        reverse["faces"]["t2"] = ("B", "D", "C")
        invalid.append(reverse)
        offplane = copy.deepcopy(target)
        offplane["nodes"]["D"] = (F(0), F(1), F(1))
        invalid.append(offplane)
        before = state(self.store)
        plans = PlanService(self.store)
        for mesh in invalid:
            with self.subTest(mesh=mesh):
                with self.assertRaises(ValueError):
                    plans.create_retriangulation_plan("reusable", revision, mesh, [])
                self.assertEqual(state(self.store), before)
        with self.assertRaises(KeyError):
            plans.create_retriangulation_plan("missing", 999, target, samples)
        self.assertEqual(state(self.store), before)
        plan = plans.create_retriangulation_plan("reusable", revision, target, samples)
        self.assertEqual(plans.create_retriangulation_plan(
            "reusable", revision, target, samples), plan)
        with self.assertRaises(ConflictError):
            plans.create_retriangulation_plan("reusable", revision, target, [])
        with self.assertRaises(ConflictError):
            plans.create_plan("reusable", revision, [])
        old = plans.create_plan("old-name", revision, [])
        with self.assertRaises(ConflictError):
            plans.create_retriangulation_plan("old-name", revision, target, samples)
        self.assertIsInstance(AnalysisService(self.store).execute(old), int)

    def test_publication_rollback_and_retry(self):
        source, fields, target, samples = fan_case()
        revision = SourceCatalog(self.store).put_source("source", source, fields)["revision_id"]
        plan = PlanService(self.store).create_retriangulation_plan("plan", revision, target, samples)
        before = state(self.store)
        for wanted in ("compute_after_samples", "publication_after_result", "publication_after_receipt"):
            def fail(stage):
                if stage == wanted:
                    raise sqlite3.OperationalError("public publication boundary")
            self.store.fault = fail
            with self.assertRaises(sqlite3.OperationalError):
                AnalysisService(self.store).execute(plan)
            self.assertEqual(state(self.store), before)
        self.store.fault = None
        self.store.connection.execute(
            "CREATE TRIGGER reject_completion BEFORE INSERT ON completion_receipts "
            "BEGIN SELECT RAISE(ABORT,'retriangulation publication rejected'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            AnalysisService(self.store).execute(plan)
        self.assertEqual(state(self.store), before)
        self.store.connection.execute("DROP TRIGGER reject_completion")
        result_id = AnalysisService(self.store).execute(plan)
        self.assertEqual(AnalysisService(self.store).execute(plan), result_id)
        self.assertEqual(ResultReader(self.store).read_result(result_id)["payload"]["face_totals"]["t1"], F(9, 7))

    def test_reopen_frozen_delivery(self):
        with tempfile.TemporaryDirectory(prefix="retriangulation-case-") as directory:
            actual = frozen_delivery(Path(directory) / "archive.sqlite")
        self.assertEqual(actual["BO"], "-4/7")
        self.assertEqual(actual["O"], "8/3")
        self.assertEqual(actual["totals"], {"t1": "9/7", "t2": "12/7", "t3": "3", "t4": "2"})


def frozen_delivery(path):
    source, fields, target, samples = fan_case()
    store = ArchiveStore.open(path)
    try:
        catalog, plans = SourceCatalog(store), PlanService(store)
        revision = catalog.put_source("frozen-source", source, fields)["revision_id"]
        plan = plans.create_retriangulation_plan("frozen-plan", revision, target, samples)
        original_plan = plans.read_plan(plan).binding()
        target["nodes"]["O"] = (F(1, 4), F(1, 4), F(0))
        samples.clear()
        detached_plan = plans.read_plan(plan).binding()
        detached_plan["target_mesh"]["nodes"]["O"] = (F(99), F(0), F(0))
        assert plans.read_plan(plan).binding() == original_plan
        fields["edge_integrals"][("A", "B")] = F(9)
        fields["node_scalars"]["A"] = F(99)
        fields["face_totals"]["s1"] = F(99)
        catalog.put_source("upstream-correction", source, fields)
        result_id = AnalysisService(store).execute(plan)
        result = ResultReader(store).read_result(result_id)
        raw = ResultExporter(ResultReader(store)).export_result(result_id)
        assert result["payload"]["target"]["fields"]["edge_integrals"][("B", "O")] == F(-4, 7)
        changed = ResultReader(store).read_result(result_id)
        changed["payload"]["target"]["fields"]["node_scalars"]["O"] = F(99)
        changed["payload"]["transfer"]["edges"].clear()
        assert ResultReader(store).read_result(result_id) == result
    finally:
        store.close()
    store = ArchiveStore.open(path)
    try:
        forbidden = {"source_revisions", "source_requests", "source_head", "analysis_plans"}
        denied = []
        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ and table in forbidden:
                denied.append(table)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        store.connection.set_authorizer(authorize)
        assert ResultReader(store).read_result(result_id) == result
        assert ResultExporter(ResultReader(store)).export_result(result_id) == raw
        assert denied == []
    finally:
        store.close()
    exported = decode_payload(raw)["result"]
    assert exported == result
    p = exported["payload"]
    assert canonical_digest(p) == exported["fingerprint"]
    assert canonical_digest(p["input"]) == p["binding"]["source_digest"]
    case = unittest.TestCase()
    check_geometry_evidence(case, p)
    assert p["face_totals"] == {"t1": F(9, 7), "t2": F(12, 7), "t3": F(3), "t4": F(2)}
    assert [s["value"] for s in p["samples"]] == [F(14, 9), F(8, 3)]
    return {"BO": str(p["target"]["fields"]["edge_integrals"][("B", "O")]),
            "O": str(p["target"]["fields"]["node_scalars"]["O"]),
            "totals": {f: str(v) for f, v in p["face_totals"].items()},
            "frozen_revision": p["binding"]["revision_id"],
            "source_AB": str(p["input"]["fields"]["edge_integrals"][("A", "B")])}


def fixtures():
    actual = []
    for i, maker in enumerate((diagonal_case, fan_case, concave_case, vertical_renamed_case)):
        source, fields, target, _ = maker()
        mesh = normalize_mesh(source)
        normalize_fields(mesh, fields)
        normalize_mesh(target)
        store = ArchiveStore.open()
        try:
            revision = SourceCatalog(store).put_source("fixture", source, fields)["revision_id"]
            plan = PlanService(store).create_plan("v1-fixture", revision, [])
            result = ResultReader(store).read_result(AnalysisService(store).execute(plan))
            assert result["payload"]["input"]["fields"] == fields
            actual.append({"case": maker.__name__, "source_faces": len(source["faces"]),
                           "target_faces": len(target["faces"]),
                           "v1_face_total": str(result["payload"]["balance"]["face_total"])})
        finally:
            store.close()
    print(json.dumps({"fixtures": actual}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fixtures", dest="mode", action="store_const", const="--fixtures")
    group.add_argument("--verify", dest="mode", action="store_const", const="--verify")
    group.add_argument("--demo", dest="mode", action="store_const", const="--demo")
    args = parser.parse_args()
    if args.mode == "--fixtures":
        fixtures()
    elif args.mode == "--verify":
        suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(RetriangulationCases))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(not result.wasSuccessful())
    else:
        with tempfile.TemporaryDirectory(prefix="retriangulation-demo-") as directory:
            actual = frozen_delivery(Path(directory) / "archive.sqlite")
        expected = {"BO": "-4/7", "O": "8/3",
                    "totals": {"t1": "9/7", "t2": "12/7", "t3": "3", "t4": "2"},
                    "frozen_revision": 1, "source_AB": "1"}
        assert actual == expected
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "retriangulation.nonuniform_frozen",
            "expected": expected, "actual": actual,
            "oracle": "independent rational hand values in RETRIANGULATION.md"
        }, sort_keys=True))


if __name__ == "__main__":
    main()
