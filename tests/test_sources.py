import copy
import sqlite3
import unittest
from fractions import Fraction as F
from pathlib import Path
from surface.sources.storage import ArchiveStore
from surface.sources.catalog import SourceCatalog
from surface.sources.revisions import RevisionRepository
from surface.sources.mesh_input import normalize_mesh
from surface.sources.field_input import normalize_fields
from surface.fields.bindings import bind_fields
from surface.results.checking import load_solver_sample


def fixture():
    mesh = {"nodes": {"A": (F(0), F(0), F(0)), "B": (F(1), F(0), F(0)),
                       "C": (F(0), F(1), F(0)), "D": (F(1), F(1), F(0))},
            "faces": {"f1": ("A", "B", "C"), "f2": ("B", "D", "C")}}
    fields = {"node_scalars": {"A": F(0), "B": F(2), "C": F(4), "D": F(6)},
              "edge_integrals": {("A", "B"): F(1, 3), ("A", "C"): F(1, 7),
                                 ("B", "C"): F(2, 5), ("B", "D"): F(3, 7),
                                 ("C", "D"): F(-1, 2)},
              "face_totals": {"f1": F(0), "f2": F(-2, 3)}}
    samples = [{"face": "f1", "weights": (F(1), F(0), F(0))},
               {"face": "f1", "weights": (F(1, 2), F(1, 2), F(0))},
               {"face": "f1", "weights": (F(1, 2), F(1, 3), F(1, 6))},
               {"face": "f2", "weights": (F(0), F(1, 2), F(1, 2))},
               {"face": "f1", "weights": (F(1, 2), F(1, 3), F(1, 6))}]
    return mesh, fields, samples


def state(store):
    return {table: store.connection.execute("SELECT * FROM " + table + " ORDER BY 1").fetchall()
            for table in ("source_revisions", "source_requests", "source_head",
                          "analysis_plans", "analysis_results", "completion_receipts")}


def fail_at(target):
    def failure(stage):
        if stage == target:
            raise sqlite3.OperationalError("injected transaction interruption")
    return failure


class TestSource(unittest.TestCase):
    def setUp(self):
        self.store = ArchiveStore.open()
        self.addCleanup(self.store.close)
        self.catalog = SourceCatalog(self.store)

    def test_exact_registration(self):
        mesh, fields, _ = fixture()
        submitted = copy.deepcopy((mesh, fields))
        first = self.catalog.put_source("first", mesh, fields)
        stored = self.catalog.read_source(first["revision_id"])
        self.assertEqual(stored["mesh"]["faces"], {"f1": ("A", "B", "C"), "f2": ("B", "D", "C")})
        self.assertEqual(stored["mesh"]["edges"],
                         [("A", "B"), ("A", "C"), ("B", "C"), ("B", "D"), ("C", "D")])
        self.assertEqual(stored["mesh"]["face_edges"]["f1"],
                         [{"edge": ("A", "B"), "sign": 1}, {"edge": ("B", "C"), "sign": 1},
                          {"edge": ("A", "C"), "sign": -1}])
        self.assertEqual(stored["mesh"]["face_edges"]["f2"],
                         [{"edge": ("B", "D"), "sign": 1}, {"edge": ("C", "D"), "sign": -1},
                          {"edge": ("B", "C"), "sign": -1}])
        reversed_mesh = {name: dict(reversed(list(items.items()))) for name, items in mesh.items()}
        reversed_fields = {name: dict(reversed(list(items.items()))) for name, items in fields.items()}
        self.assertEqual(self.catalog.put_source("first", reversed_mesh, reversed_fields), first)
        fields["node_scalars"]["A"] = F(88)
        mesh["nodes"]["A"] = (F(50), F(0), F(0))
        stored["fields"]["edge_integrals"][("A", "B")] = F(99)
        stored["mesh"]["faces"]["f1"] = ("C", "B", "A")
        self.assertEqual(self.catalog.read_source(first["revision_id"])["fields"], submitted[1])
        self.assertEqual(self.catalog.read_source(first["revision_id"])["mesh"]["nodes"], submitted[0]["nodes"])
        corrected_mesh, corrected_fields, _ = fixture()
        corrected_fields["face_totals"]["f1"] = F(3)
        second = self.catalog.put_source("correction", corrected_mesh, corrected_fields)
        before = state(self.store)
        self.store.initialize()
        self.assertEqual(state(self.store), before)
        self.assertEqual(self.catalog.current_revision(), second["revision_id"])
        vertical = {"nodes": {"p": (F(0), F(0), F(0)), "q": (F(0), F(1), F(0)),
                              "r": (F(0), F(0), F(1))}, "faces": {"v": ("p", "q", "r")}}
        vfields = {"node_scalars": {k: F(0) for k in vertical["nodes"]},
                   "edge_integrals": {("p", "q"): F(1), ("p", "r"): F(2), ("q", "r"): F(3)},
                   "face_totals": {"v": F(-1)}}
        receipt = self.catalog.put_source("vertical", vertical, vfields)
        self.assertEqual(self.catalog.read_source(receipt["revision_id"])["mesh"]["nodes"], vertical["nodes"])
        duplicate = {"nodes": {"a": (F(0), F(0), F(0)), "b": (F(1), F(0), F(0)),
                               "c": (F(0), F(1), F(0)), "x": (F(0), F(0), F(0)),
                               "y": (F(1), F(0), F(0)), "z": (F(0), F(1), F(0))},
                     "faces": {"one": ("a", "b", "c"), "two": ("x", "y", "z")}}
        dfields = {"node_scalars": {k: F(i) for i, k in enumerate(duplicate["nodes"])},
                   "edge_integrals": {("a", "b"): F(1), ("a", "c"): F(2), ("b", "c"): F(3),
                                      ("x", "y"): F(11), ("x", "z"): F(12), ("y", "z"): F(13)},
                   "face_totals": {"one": F(0), "two": F(-7)}}
        d = self.catalog.put_source("identities", duplicate, dfields)
        got = self.catalog.read_source(d["revision_id"])
        self.assertEqual(len(got["mesh"]["nodes"]), 6)
        self.assertEqual(len(got["mesh"]["edges"]), 6)
        self.assertEqual(got["fields"], dfields)
        revision = RevisionRepository(self.store).read(d["revision_id"])
        bundle = bind_fields(d["revision_id"], revision.mesh, revision.fields)
        self.assertEqual(bundle.edge_value("y", "x"), F(-11))
        self.assertEqual(bundle.edge_value("a", "b"), F(1))

    def test_reject_incomplete_revision(self):
        mesh, fields, _ = fixture()
        self.catalog.put_source("good", mesh, fields)
        original = state(self.store)
        bad_meshes = []
        missing = copy.deepcopy(mesh)
        del missing["nodes"]["C"]
        bad_meshes.append(missing)
        collinear = copy.deepcopy(mesh)
        collinear["nodes"]["C"] = (F(2), F(0), F(0))
        bad_meshes.append(collinear)
        repeated = copy.deepcopy(mesh)
        repeated["faces"]["f1"] = ("A", "A", "C")
        bad_meshes.append(repeated)
        overfull = copy.deepcopy(mesh)
        overfull["faces"]["f3"] = ("A", "B", "C")
        bad_meshes.append(overfull)
        nonfraction = copy.deepcopy(mesh)
        nonfraction["nodes"]["A"] = (0, F(0), F(0))
        bad_meshes.append(nonfraction)
        invalid_mesh, _, _ = load_solver_sample(
            Path(__file__).resolve().parents[1] / "samples" / "invalid.json")
        bad_meshes.append(invalid_mesh)
        for bad in bad_meshes:
            with self.subTest(mesh=bad):
                with self.assertRaises(ValueError):
                    self.catalog.put_source("rejected", bad, fields)
                self.assertEqual(state(self.store), original)
        for name in fields:
            for mode in ("missing", "extra", "replacement", "bool", "none", "float"):
                bad = copy.deepcopy(fields)
                key = next(iter(bad[name]))
                extra = ("X", "Y") if name == "edge_integrals" else "alien"
                if mode == "missing":
                    del bad[name][key]
                elif mode == "extra":
                    bad[name][extra] = F(0)
                elif mode == "replacement":
                    bad[name][extra] = bad[name].pop(key)
                else:
                    bad[name][key] = {"bool": True, "none": None, "float": 0.0}[mode]
                with self.subTest(field=name, mode=mode):
                    with self.assertRaises(ValueError):
                        self.catalog.put_source("rejected", mesh, bad)
                    self.assertEqual(state(self.store), original)
        for stage in ("source_after_revision", "source_after_request", "source_after_head"):
            self.store.fault = fail_at(stage)
            with self.assertRaises(sqlite3.OperationalError):
                self.catalog.put_source("retry", mesh, fields)
            self.assertEqual(state(self.store), original)
        self.store.fault = None
        retry = self.catalog.put_source("retry", mesh, fields)
        self.assertEqual(self.catalog.current_revision(), retry["revision_id"])
        zero = {name: {key: F(0) for key in mapping} for name, mapping in fields.items()}
        z = self.catalog.put_source("zero", mesh, zero)
        self.assertEqual(self.catalog.read_source(z["revision_id"])["fields"], zero)

    def test_codec_and_archive_version(self):
        from surface.results.codec import encode_payload, decode_payload
        value = {("a", "b"): F(2**90 + 1, 2**91 + 3), "ordered": [F(-1, 7), F(0)]}
        self.assertEqual(decode_payload(encode_payload(value)), value)
        for raw in (b'{"format":"surface-exact","version":1,"payload":{"$fraction":[1,0]}}',
                    b'{"format":"surface-exact","version":1,"payload":{"$fraction":[true,1]}}',
                    b'{"format":"surface-exact","version":1,"payload":{"$fraction":[2,4]}}',
                    b'{"format":"surface-exact","version":2,"payload":0}',
                    b'{"format":"surface-exact","version":1,"payload":NaN}',
                    b'{"format":"surface-exact","version":1,"payload":0,"payload":1}'):
            with self.assertRaises(ValueError):
                decode_payload(raw)
        with self.assertRaises(ValueError):
            encode_payload(0.1)
        self.store.connection.execute("UPDATE archive_format SET version=2")
        with self.assertRaises(ValueError):
            self.store.initialize()
        self.store.connection.execute("UPDATE archive_format SET version=1")
        self.store.initialize()


if __name__ == "__main__":
    unittest.main()
