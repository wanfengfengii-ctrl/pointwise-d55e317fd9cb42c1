import copy
import sqlite3
import unittest
from fractions import Fraction as F
from tests.test_sources import fixture, state, fail_at
from surface.sources.storage import ArchiveStore
from surface.sources.catalog import SourceCatalog
from surface.sources.revisions import ConflictError
from surface.tasks.plans import PlanService
from surface.tasks.execution import AnalysisService
from surface.results.reading import ResultReader


class TestBindings(unittest.TestCase):
    def setUp(self):
        self.store = ArchiveStore.open()
        self.addCleanup(self.store.close)
        self.catalog = SourceCatalog(self.store)
        self.plans = PlanService(self.store)

    def test_request_replay_and_conflict(self):
        mesh, fields, samples = fixture()
        first = self.catalog.put_source("shared-name", mesh, fields)
        self.assertEqual(self.catalog.put_source("shared-name", mesh, fields), first)
        changed = copy.deepcopy(fields)
        changed["node_scalars"]["A"] = F(9)
        second = self.catalog.put_source("correction", mesh, changed)
        original = state(self.store)
        self.assertEqual(self.catalog.put_source("shared-name", mesh, fields), first)
        self.assertEqual(self.catalog.current_revision(), second["revision_id"])
        self.assertEqual(state(self.store), original)
        alternatives = []
        for name in fields:
            bad = copy.deepcopy(fields)
            key = next(iter(bad[name]))
            bad[name][key] += F(1, 11)
            alternatives.append((mesh, bad))
        reverse = copy.deepcopy(mesh)
        reverse["faces"]["f1"] = ("A", "C", "B")
        alternatives.append((reverse, fields))
        coordinate = copy.deepcopy(mesh)
        coordinate["nodes"]["A"] = (F(-1), F(0), F(0))
        alternatives.append((coordinate, fields))
        for m, f in alternatives:
            with self.assertRaises(ConflictError):
                self.catalog.put_source("shared-name", m, f)
            self.assertEqual(state(self.store), original)
        plan = self.plans.create_plan("shared-name", first["revision_id"], samples)
        self.assertEqual(self.plans.create_plan("shared-name", first["revision_id"], samples), plan)
        plan_state = state(self.store)
        for revision_id, queue in ((second["revision_id"], samples),
                                   (first["revision_id"], list(reversed(samples))),
                                   (first["revision_id"], []),
                                   (first["revision_id"], samples + [samples[0]])):
            with self.assertRaises(ConflictError):
                self.plans.create_plan("shared-name", revision_id, queue)
            self.assertEqual(state(self.store), plan_state)
        result = ResultReader(self.store).read_result(AnalysisService(self.store).execute(plan))
        self.assertEqual(result["payload"]["binding"]["revision_id"], first["revision_id"])

    def test_invalid_barycentric_plan(self):
        mesh, fields, samples = fixture()
        revision = self.catalog.put_source("input", mesh, fields)["revision_id"]
        initial = state(self.store)
        bad = [{"face": "missing", "weights": (F(1), F(0), F(0))},
               {"face": "f1", "weights": (F(-1), F(1), F(1))},
               {"face": "f1", "weights": (F(1, 2), F(0), F(0))},
               {"face": "f1", "weights": (1, F(0), F(0))},
               {"face": "f1", "weights": (True, F(0), F(0))},
               {"face": "f1", "weights": (None, F(0), F(1))},
               {"face": "f1", "weights": (F(1), F(0))},
               {"face": "f1", "weights": (F(1), F(0), F(0)), "extra": 0}]
        for sample in bad:
            with self.assertRaises(ValueError):
                self.plans.create_plan("reusable", revision, [samples[0], sample])
            self.assertEqual(state(self.store), initial)
        with self.assertRaises(KeyError):
            self.plans.create_plan("missing", 999, [])
        self.assertEqual(state(self.store), initial)
        self.store.fault = fail_at("plan_after_insert")
        with self.assertRaises(sqlite3.OperationalError):
            self.plans.create_plan("reusable", revision, samples)
        self.assertEqual(state(self.store), initial)
        self.store.fault = None
        valid = self.plans.create_plan("reusable", revision, samples)
        self.assertEqual(list(self.plans.read_plan(valid).samples), samples)
        with self.assertRaises(KeyError):
            self.catalog.read_source(999)
        with self.assertRaises(KeyError):
            AnalysisService(self.store).execute(999)
        with self.assertRaises(KeyError):
            ResultReader(self.store).read_result(999)


if __name__ == "__main__":
    unittest.main()
