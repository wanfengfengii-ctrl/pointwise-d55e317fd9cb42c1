import copy
import json
import sqlite3
import tempfile
import unittest
from fractions import Fraction as F
from pathlib import Path
from tests.test_sources import fixture, state, fail_at
from surface.sources.storage import ArchiveStore
from surface.sources.catalog import SourceCatalog
from surface.sources.revisions import RevisionRepository
from surface.tasks.plans import PlanService
from surface.tasks.execution import AnalysisService
from surface.tasks.receipts import ReceiptRepository
from surface.tasks.publication import publish_result
from surface.results.repository import ResultRepository
from surface.results.reading import ResultReader
from surface.results.exporting import ResultExporter
from surface.results.checking import check_export
from surface.results.codec import encode_payload, decode_payload, canonical_digest


class TestResults(unittest.TestCase):
    def test_publication_rollback(self):
        store = ArchiveStore.open()
        self.addCleanup(store.close)
        mesh, fields, samples = fixture()
        revision = SourceCatalog(store).put_source("source", mesh, fields)["revision_id"]
        plan = PlanService(store).create_plan("plan", revision, samples)
        analysis = AnalysisService(store)
        initial = state(store)
        for stage in ("compute_after_samples", "publication_after_result", "publication_after_receipt"):
            store.fault = fail_at(stage)
            with self.assertRaises(sqlite3.OperationalError):
                analysis.execute(plan)
            self.assertEqual(state(store), initial)
        store.fault = None
        result_id = analysis.execute(plan)
        result = ResultReader(store).read_result(result_id)
        self.assertEqual(result["payload"]["circulations"], {"f1": F(62, 105), "f2": F(37, 70)})
        published = state(store)
        store.fault = fail_at("compute_after_samples")
        self.assertEqual(analysis.execute(plan), result_id)
        self.assertEqual(state(store), published)
        second_plan = PlanService(store).create_plan("next", revision, [])
        before = state(store)
        with self.assertRaises(sqlite3.OperationalError):
            analysis.execute(second_plan)
        self.assertEqual(state(store), before)
        self.assertEqual(ResultReader(store).read_result(result_id), result)
        store.fault = None
        other = ArchiveStore.open()
        try:
            with self.assertRaises(ValueError):
                publish_result(store, ResultRepository(other), ReceiptRepository(store),
                               plan, result["payload"])
            self.assertEqual(state(store), before)
        finally:
            other.close()

    def test_frozen_reopen_and_export(self):
        with tempfile.TemporaryDirectory(prefix="surface-regression-") as directory:
            path = Path(directory) / "archive.sqlite"
            store = ArchiveStore.open(path)
            try:
                mesh, fields, samples = fixture()
                catalog = SourceCatalog(store)
                source = catalog.put_source("first", mesh, fields)
                plan = PlanService(store).create_plan("first", source["revision_id"], samples)
                result_id = AnalysisService(store).execute(plan)
                reader = ResultReader(store)
                original = reader.read_result(result_id)
                raw = ResultExporter(reader).export_result(result_id)
                self.assertEqual(raw, ResultExporter(reader).export_result(result_id))
                fields["node_scalars"]["A"] = F(2**100 + 1, 2**101 + 3)
                fields["edge_integrals"][("A", "B")] = F(2**120 + 1, 2**121 + 7)
                current = catalog.put_source("corrected", mesh, fields)
                huge_plan = PlanService(store).create_plan("huge", current["revision_id"],
                                                          [{"face": "f1", "weights": (F(1), F(0), F(0))}])
                huge_id = AnalysisService(store).execute(huge_plan)
                huge = reader.read_result(huge_id)
                detached = reader.read_result(result_id)
                detached["payload"]["input"]["mesh"]["nodes"]["A"] = (F(99), F(0), F(0))
                detached["payload"]["input"]["fields"]["node_scalars"]["A"] = F(99)
                detached["payload"]["samples"].reverse()
                detached["payload"]["balance"]["face_gaps"]["f1"] = F(99)
                self.assertEqual(reader.read_result(result_id), original)
                snapshot = state(store)
                store.fault = fail_at("export_before_encode")
                with self.assertRaises(sqlite3.OperationalError):
                    ResultExporter(reader).export_result(result_id)
                self.assertEqual(state(store), snapshot)
                store.fault = None
                store.initialize()
                self.assertEqual(state(store), snapshot)
                self.assertEqual(catalog.current_revision(), current["revision_id"])
            finally:
                store.close()
            store = ArchiveStore.open(path)
            try:
                self.assertEqual(SourceCatalog(store).current_revision(), current["revision_id"])
                self.assertEqual(SourceCatalog(store).read_source(source["revision_id"])["fields"],
                                 original["payload"]["input"]["fields"])
                self.assertEqual(ResultReader(store).read_result(huge_id), huge)
                self.assertEqual(huge["payload"]["samples"][0]["value"], fields["node_scalars"]["A"])
                self.assertEqual(huge["payload"]["input"]["fields"]["edge_integrals"][("A", "B")],
                                 fields["edge_integrals"][("A", "B")])
                # SQLite authorizer is a real access boundary: historical result
                # delivery must succeed while every source/plan read is denied.
                denied = []
                forbidden = {"source_revisions", "source_requests", "source_head", "analysis_plans"}
                def authorize(action, table, column, database, trigger):
                    if action == sqlite3.SQLITE_READ and table in forbidden:
                        denied.append((table, column))
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                store.connection.set_authorizer(authorize)
                reader = ResultReader(store)
                self.assertEqual(reader.read_result(result_id), original)
                self.assertEqual(ResultExporter(reader).export_result(result_id), raw)
                self.assertEqual(denied, [])
                huge_export = ResultExporter(reader).export_result(huge_id)
                check_export(huge_export)
                store.connection.set_authorizer(None)
                before = state(store)
                # A real SQLite trigger interrupts publication after payload insertion.
                retry_plan = PlanService(store).create_plan("disk-retry", source["revision_id"], [])
                store.connection.execute(
                    "CREATE TRIGGER reject_completion BEFORE INSERT ON completion_receipts BEGIN SELECT RAISE(ABORT,'publication rejected'); END")
                before_failure = state(store)
                with self.assertRaises(sqlite3.IntegrityError):
                    AnalysisService(store).execute(retry_plan)
                self.assertEqual(state(store), before_failure)
                store.connection.execute("DROP TRIGGER reject_completion")
            finally:
                store.close()
            store = ArchiveStore.open(path)
            try:
                retry_id = AnalysisService(store).execute(retry_plan)
                self.assertEqual(AnalysisService(store).execute(retry_plan), retry_id)
                self.assertEqual(ResultReader(store).read_result(result_id), original)
                check_export(ResultExporter(ResultReader(store)).export_result(retry_id))
                row = store.connection.execute(
                    "SELECT fingerprint,payload FROM analysis_results WHERE id=?", (result_id,)).fetchone()
                broken_values = []
                bad_structure = copy.deepcopy(original["payload"])
                del bad_structure["samples"]
                broken_values.append((canonical_digest(bad_structure), encode_payload(bad_structure)))
                bad_binding = copy.deepcopy(original["payload"])
                bad_binding["binding"]["source_digest"] = "not-the-fixed-source"
                broken_values.append((canonical_digest(bad_binding), encode_payload(bad_binding)))
                tree = json.loads(row[1])
                def break_denominator(value):
                    if type(value) is dict:
                        if "$fraction" in value:
                            value["$fraction"][1] = 0
                            return True
                        return any(break_denominator(item) for item in value.values())
                    if type(value) is list:
                        return any(break_denominator(item) for item in value)
                    return False
                self.assertTrue(break_denominator(tree))
                broken_values.append((row[0], json.dumps(tree).encode("ascii")))
                for digest, broken in broken_values:
                    store.connection.execute(
                        "UPDATE analysis_results SET fingerprint=?,payload=? WHERE id=?",
                        (digest, broken, result_id))
                    store.fault = fail_at("compute_after_samples")
                    with self.assertRaises(ValueError):
                        ResultReader(store).read_result(result_id)
                    with self.assertRaises(ValueError):
                        ResultExporter(ResultReader(store)).export_result(result_id)
                    with self.assertRaises(ValueError):
                        AnalysisService(store).execute(plan)
                    store.fault = None
                store.connection.execute(
                    "UPDATE analysis_results SET fingerprint=?,payload=? WHERE id=?",
                    (row[0], row[1], result_id))
                tampered = decode_payload(raw)
                tampered["result"]["payload"]["samples"][2]["value"] += F(1)
                tampered["result"]["fingerprint"] = canonical_digest(tampered["result"]["payload"])
                with self.assertRaises(ValueError):
                    check_export(encode_payload(tampered))
                self.assertEqual(ResultReader(store).read_result(result_id), original)
            finally:
                store.close()
            # Offline checking happens after the connection has been closed.
            report = check_export(raw)
            self.assertEqual(report.boundary_total, F(47, 42))
            self.assertEqual(report.internal_total, F(0))
            self.assertEqual(report.gap_total, F(25, 14))
            exported = decode_payload(raw)["result"]
            self.assertEqual(exported, original)
            self.assertEqual(exported["payload"]["samples"][2]["value"], F(4, 3))
            self.assertEqual(exported["payload"]["input"]["fields"]["edge_integrals"][("A", "B")], F(1, 3))


if __name__ == "__main__":
    unittest.main()
