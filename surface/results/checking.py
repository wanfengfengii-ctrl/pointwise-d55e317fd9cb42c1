import argparse
import json
from dataclasses import dataclass
from fractions import Fraction
from surface.results.codec import decode_payload, canonical_digest


@dataclass(frozen=True)
class CheckReport:
    faces: int
    samples: int
    boundary_total: Fraction
    internal_total: Fraction
    gap_total: Fraction


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _cross3(u, v):
    return (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])


def _traverse_mesh(mesh):
    _require(type(mesh) is dict and set(mesh) == {"nodes", "faces", "edges", "face_edges"},
             "invalid ordinary mesh")
    nodes, faces = mesh["nodes"], mesh["faces"]
    _require(type(nodes) is dict and bool(nodes) and type(faces) is dict
             and 1 <= len(faces) <= 60, "invalid geometry size")
    for node, xyz in nodes.items():
        _require(type(node) is str and bool(node) and type(xyz) is tuple
                 and len(xyz) == 3 and all(isinstance(x, Fraction) for x in xyz),
                 "invalid node coordinates")
    uses, face_terms = {}, {}
    for face, vertices in sorted(faces.items()):
        _require(type(face) is str and bool(face) and type(vertices) is tuple
                 and len(vertices) == 3 and all(type(v) is str and v in nodes for v in vertices)
                 and len(set(vertices)) == 3, "invalid face references")
        a, b, c = [nodes[node] for node in vertices]
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        cross = _cross3(u, v)
        _require(any(x != 0 for x in cross), "degenerate triangle")
        terms = []
        for i in range(3):
            start, end = vertices[i], vertices[(i + 1) % 3]
            edge = tuple(sorted((start, end)))
            sign = 1 if start < end else -1
            terms.append({"edge": edge, "sign": sign})
            uses.setdefault(edge, []).append(sign)
        face_terms[face] = terms
    _require(all(1 <= len(signs) <= 2 for signs in uses.values()), "nonmanifold edge")
    _require(mesh["edges"] == sorted(uses) and mesh["face_edges"] == face_terms,
             "saved incidence differs from original face winding")
    return nodes, faces, uses, face_terms


def _traverse_fields(fields, nodes, uses, faces):
    _require(type(fields) is dict and set(fields) == {
        "node_scalars", "edge_integrals", "face_totals"}, "invalid fields")
    for name, identities in (("node_scalars", nodes), ("edge_integrals", uses),
                             ("face_totals", faces)):
        _require(type(fields[name]) is dict and set(fields[name]) == set(identities)
                 and all(isinstance(x, Fraction) for x in fields[name].values()),
                 "inexact field coverage")
    return fields


def _expected_samples(nodes, faces, fields, requests):
    _require(type(requests) is list, "invalid sample sequence")
    expected = []
    for request in requests:
        _require(type(request) is dict and set(request) == {"face", "weights"},
                 "invalid request")
        face, weights = request["face"], request["weights"]
        _require(type(face) is str and face in faces and type(weights) is tuple
                 and len(weights) == 3 and all(isinstance(w, Fraction) and w >= 0 for w in weights)
                 and sum(weights) == 1, "invalid exact barycentric weights")
        vertices = faces[face]
        position = tuple(sum((weights[i] * nodes[vertices[i]][axis] for i in range(3)),
                             Fraction(0)) for axis in range(3))
        value = sum((weights[i] * fields["node_scalars"][vertices[i]]
                     for i in range(3)), Fraction(0))
        expected.append({"face": face, "weights": weights,
                         "position": position, "value": value})
    return expected


def _check_stored_samples(expected, stored):
    _require(type(stored) is list, "invalid sample sequence")
    for sample in stored:
        _require(type(sample) is dict and set(sample) == {"face", "weights", "position", "value"}
                 and isinstance(sample["value"], Fraction)
                 and type(sample["position"]) is tuple and len(sample["position"]) == 3
                 and all(isinstance(x, Fraction) for x in sample["position"]),
                 "nonrational sample evidence")
    _require(stored == expected, "sample position/value/order mismatch")


def _traverse_ledger(face_terms, uses, fields):
    integrals = fields["edge_integrals"]
    circulations = {face: sum((term["sign"] * integrals[term["edge"]]
                              for term in terms), Fraction(0))
                    for face, terms in face_terms.items()}
    totals = fields["face_totals"]
    boundary, internal = [], []
    for edge, signs in sorted(uses.items()):
        coefficient = sum(signs)
        item = {"edge": edge, "coefficient": coefficient,
                "value": coefficient * integrals[edge]}
        (boundary if len(signs) == 1 else internal).append(item)
    bt = sum((item["value"] for item in boundary), Fraction(0))
    it = sum((item["value"] for item in internal), Fraction(0))
    ct, ft = sum(circulations.values(), Fraction(0)), sum(totals.values(), Fraction(0))
    expected_balance = {
        "face_gaps": {face: circulations[face] - totals[face] for face in sorted(face_terms)},
        "boundary_terms": boundary, "internal_terms": internal,
        "boundary_total": bt, "internal_total": it,
        "circulation_total": ct, "face_total": ft, "gap_total": ct - ft}
    return circulations, expected_balance, bt, it


def _check_ledger_records(p, fields, circulations, expected_balance, bt, it):
    _require(type(p["circulations"]) is dict
             and all(isinstance(x, Fraction) for x in p["circulations"].values())
             and p["circulations"] == circulations, "incorrect face circulation")
    totals = fields["face_totals"]
    _require(type(p["face_totals"]) is dict
             and all(isinstance(x, Fraction) for x in p["face_totals"].values())
             and p["face_totals"] == totals, "incorrect saved face totals")
    actual = p["balance"]
    _require(type(actual) is dict and set(actual) == set(expected_balance),
             "incomplete balance")
    for key in ("boundary_total", "internal_total", "circulation_total", "face_total", "gap_total"):
        _require(isinstance(actual[key], Fraction), "nonrational balance")
    _require(type(actual["face_gaps"]) is dict
             and all(isinstance(x, Fraction) for x in actual["face_gaps"].values()),
             "nonrational face gaps")
    for key in ("boundary_terms", "internal_terms"):
        _require(type(actual[key]) is list, "invalid ledger sequence")
        for item in actual[key]:
            _require(type(item) is dict and set(item) == {"edge", "coefficient", "value"}
                     and type(item["coefficient"]) is int
                     and isinstance(item["value"], Fraction), "nonrational edge ledger")
    ct = expected_balance["circulation_total"]
    _require(actual == expected_balance and ct == bt + it,
             "quality ledger mismatch")


def _check(p):
    _require(type(p) is dict and set(p) == {
        "kind", "binding", "input", "requests", "samples", "circulations",
        "face_totals", "balance"}, "invalid payload members")
    _require(p["kind"] == "ordinary-analysis-v1", "unsupported analysis")
    binding, source = p["binding"], p["input"]
    _require(type(binding) is dict and set(binding) == {
        "plan_id", "plan_digest", "revision_id", "source_digest", "convention"},
        "incomplete binding")
    _require(all(type(binding[k]) is int and binding[k] > 0
                 for k in ("plan_id", "revision_id")), "invalid binding IDs")
    _require(binding["convention"] == "ordinary-surface-v1", "unsupported convention")
    _require(type(source) is dict and set(source) == {"mesh", "fields"},
             "incomplete source snapshot")
    _require(canonical_digest(source) == binding["source_digest"], "source digest mismatch")
    nodes, faces, uses, face_terms = _traverse_mesh(source["mesh"])
    fields = _traverse_fields(source["fields"], nodes, uses, faces)
    expected_samples = _expected_samples(nodes, faces, fields, p["requests"])
    _check_stored_samples(expected_samples, p["samples"])
    plan_binding = {"revision_id": binding["revision_id"],
                    "source_digest": binding["source_digest"], "samples": p["requests"],
                    "convention": binding["convention"]}
    _require(canonical_digest(plan_binding) == binding["plan_digest"], "plan digest mismatch")
    circulations, expected_balance, bt, it = _traverse_ledger(face_terms, uses, fields)
    _check_ledger_records(p, fields, circulations, expected_balance, bt, it)
    return CheckReport(len(faces), len(expected_samples), bt, it,
                       expected_balance["gap_total"])


def _check_planar_pair(snodes, sfaces, tnodes, tfaces):
    reference = sorted(sfaces)[0]
    ra, rb, rc = (snodes[v] for v in sfaces[reference])
    normal = _cross3([rb[i] - ra[i] for i in range(3)],
                     [rc[i] - ra[i] for i in range(3)])
    for nodes in (snodes, tnodes):
        _require(len(set(nodes.values())) == len(nodes), "coincident node identities")
        for xyz in nodes.values():
            _require(sum((xyz[i] - ra[i]) * normal[i] for i in range(3)) == 0,
                     "node outside the common plane")
    for faces, nodes in ((sfaces, snodes), (tfaces, tnodes)):
        used = set()
        for vertices in faces.values():
            used.update(vertices)
            a, b, c = (nodes[v] for v in vertices)
            cross = _cross3([b[i] - a[i] for i in range(3)],
                            [c[i] - a[i] for i in range(3)])
            _require(sum(cross[i] * normal[i] for i in range(3)) > 0,
                     "face winding conflicts with the common normal")
        _require(used == set(nodes), "unused node identity")
    return normal


def _check_transfer(transfer, snodes, sfaces, sfields, tnodes, tfaces, tfields, normal):
    _require(type(transfer) is dict and set(transfer) == {"nodes", "edges", "overlaps"},
             "incomplete transfer evidence")

    def weights(value):
        return (type(value) is tuple and len(value) == 3
                and all(isinstance(w, Fraction) and w >= 0 for w in value)
                and sum(value) == 1)

    def position(face, w):
        vertices = sfaces[face]
        return tuple(sum((w[i] * snodes[vertices[i]][axis] for i in range(3)),
                         Fraction(0)) for axis in range(3))

    nodes_ev = transfer["nodes"]
    _require(type(nodes_ev) is dict and set(nodes_ev) == set(tnodes),
             "node evidence coverage")
    for node, item in nodes_ev.items():
        _require(type(item) is dict and set(item) == {"source_face", "weights"}
                 and item["source_face"] in sfaces and weights(item["weights"]),
                 "invalid node evidence")
        _require(position(item["source_face"], item["weights"]) == tnodes[node],
                 "node evidence does not reconstruct the target node")
        vertices = sfaces[item["source_face"]]
        value = sum((item["weights"][i] * sfields["node_scalars"][vertices[i]]
                     for i in range(3)), Fraction(0))
        _require(value == tfields["node_scalars"][node],
                 "transferred node scalar mismatch")
    edges_ev = transfer["edges"]
    expected_edges = set()
    for vertices in tfaces.values():
        for i in range(3):
            expected_edges.add(tuple(sorted((vertices[i], vertices[(i + 1) % 3]))))
    _require(type(edges_ev) is dict and set(edges_ev) == expected_edges,
             "edge evidence coverage")
    for edge, pieces in edges_ev.items():
        _require(type(edge) is tuple and len(edge) == 2
                 and type(pieces) is list and bool(pieces), "invalid edge evidence")
        p0, p1 = tnodes[edge[0]], tnodes[edge[1]]
        total, previous = Fraction(0), Fraction(0)
        for item in pieces:
            _require(type(item) is dict and set(item) == {
                "source_face", "interval", "start_weights", "end_weights"}
                and item["source_face"] in sfaces, "invalid edge evidence")
            interval = item["interval"]
            _require(type(interval) is tuple and len(interval) == 2
                     and all(isinstance(t, Fraction) for t in interval)
                     and interval[0] == previous and interval[0] < interval[1] <= 1,
                     "invalid edge segmentation")
            face = item["source_face"]
            vertices = sfaces[face]
            for t, w in ((interval[0], item["start_weights"]),
                         (interval[1], item["end_weights"])):
                _require(weights(w), "invalid edge evidence weights")
                expect = tuple((1 - t) * p0[axis] + t * p1[axis] for axis in range(3))
                _require(position(face, w) == expect, "edge evidence endpoint mismatch")
            start, end = item["start_weights"], item["end_weights"]
            for i in range(3):
                for j in range(i + 1, 3):
                    a, b = vertices[i], vertices[j]
                    key = (a, b) if a < b else (b, a)
                    value = sfields["edge_integrals"][key]
                    if (a, b) != key:
                        value = -value
                    mean_i = (start[i] + end[i]) / 2
                    mean_j = (start[j] + end[j]) / 2
                    total += value * (mean_i * (end[j] - start[j])
                                    - mean_j * (end[i] - start[i]))
            previous = interval[1]
        _require(previous == 1, "edge evidence does not partition the edge")
        _require(total == tfields["edge_integrals"][edge],
                 "transferred edge integral mismatch")
    overlaps = transfer["overlaps"]
    _require(type(overlaps) is list, "invalid overlap evidence")
    scale = sum(x * x for x in normal)

    def signed_area(faces, nodes, face):
        a, b, c = (nodes[v] for v in faces[face])
        cross = _cross3([b[i] - a[i] for i in range(3)],
                        [c[i] - a[i] for i in range(3)])
        return sum(cross[i] * normal[i] for i in range(3)) / scale

    seen = set()
    coverage = {face: Fraction(0) for face in sfaces}
    target_area = {face: Fraction(0) for face in tfaces}
    totals = {face: Fraction(0) for face in tfaces}
    for item in overlaps:
        _require(type(item) is dict and set(item) == {
            "source_face", "target_face", "source_weights", "source_fraction"},
            "invalid overlap evidence")
        s, t = item["source_face"], item["target_face"]
        _require(s in sfaces and t in tfaces and (s, t) not in seen,
                 "invalid overlap identities")
        seen.add((s, t))
        polygon = item["source_weights"]
        _require(type(polygon) is tuple and len(polygon) >= 3
                 and polygon[0] != polygon[-1]
                 and all(weights(w) for w in polygon), "invalid overlap polygon")
        fraction = item["source_fraction"]
        _require(isinstance(fraction, Fraction) and fraction > 0,
                 "invalid overlap fraction")
        uv = [(w[1], w[2]) for w in polygon]
        count = len(uv)
        area2 = sum((uv[i][0] * uv[(i + 1) % count][1]
                     - uv[(i + 1) % count][0] * uv[i][1]
                     for i in range(count)), Fraction(0))
        _require(area2 == fraction, "overlap fraction mismatch")
        for i in range(count):
            ux = uv[(i + 1) % count][0] - uv[i][0]
            uy = uv[(i + 1) % count][1] - uv[i][1]
            vx = uv[(i + 2) % count][0] - uv[(i + 1) % count][0]
            vy = uv[(i + 2) % count][1] - uv[(i + 1) % count][1]
            _require(ux * vy - uy * vx >= 0, "overlap polygon is not convex")
        coverage[s] += fraction
        target_area[t] += fraction * signed_area(sfaces, snodes, s)
        totals[t] += fraction * sfields["face_totals"][s]
    _require(all(value == 1 for value in coverage.values()),
             "source coverage incomplete")
    for face in tfaces:
        _require(target_area[face] == signed_area(tfaces, tnodes, face),
                 "target coverage mismatch")
        _require(totals[face] == tfields["face_totals"][face],
                 "transferred face total mismatch")


def _check_retriangulated(p):
    _require(type(p) is dict and set(p) == {
        "kind", "binding", "input", "requests", "samples", "circulations",
        "face_totals", "balance", "target", "transfer"}, "invalid payload members")
    binding, source = p["binding"], p["input"]
    _require(type(binding) is dict and set(binding) == {
        "plan_id", "plan_digest", "revision_id", "source_digest", "convention"},
        "incomplete binding")
    _require(all(type(binding[k]) is int and binding[k] > 0
                 for k in ("plan_id", "revision_id")), "invalid binding IDs")
    _require(binding["convention"] == "retriangulated-whitney-v1",
             "unsupported convention")
    _require(type(source) is dict and set(source) == {"mesh", "fields"},
             "incomplete source snapshot")
    _require(canonical_digest(source) == binding["source_digest"], "source digest mismatch")
    snodes, sfaces, suse, sterms = _traverse_mesh(source["mesh"])
    sfields = _traverse_fields(source["fields"], snodes, suse, sfaces)
    target = p["target"]
    _require(type(target) is dict and set(target) == {"mesh", "fields"},
             "incomplete target snapshot")
    tnodes, tfaces, tuse, tterms = _traverse_mesh(target["mesh"])
    tfields = _traverse_fields(target["fields"], tnodes, tuse, tfaces)
    plan_binding = {"revision_id": binding["revision_id"],
                    "source_digest": binding["source_digest"], "samples": p["requests"],
                    "convention": binding["convention"], "target_mesh": target["mesh"]}
    _require(canonical_digest(plan_binding) == binding["plan_digest"],
             "plan digest mismatch")
    expected_samples = _expected_samples(tnodes, tfaces, tfields, p["requests"])
    _check_stored_samples(expected_samples, p["samples"])
    normal = _check_planar_pair(snodes, sfaces, tnodes, tfaces)
    _check_transfer(p["transfer"], snodes, sfaces, sfields,
                    tnodes, tfaces, tfields, normal)
    circulations, expected_balance, bt, it = _traverse_ledger(tterms, tuse, tfields)
    _check_ledger_records(p, tfields, circulations, expected_balance, bt, it)
    return CheckReport(len(tfaces), len(expected_samples), bt, it,
                       expected_balance["gap_total"])


def check_result_payload(payload):
    try:
        if type(payload) is dict and payload.get("kind") == "retriangulated-analysis-v1":
            return _check_retriangulated(payload)
        return _check(payload)
    except (KeyError, TypeError, IndexError, AttributeError, ZeroDivisionError) as exc:
        raise ValueError("invalid offline quality record") from exc


def check_export(raw):
    envelope = decode_payload(raw)
    _require(type(envelope) is dict and set(envelope) == {
        "export_kind", "export_version", "result"}
        and envelope["export_kind"] == "surface-quality-record"
        and type(envelope["export_version"]) is int and envelope["export_version"] == 1,
        "unsupported export")
    result = envelope["result"]
    _require(type(result) is dict and set(result) == {"result_id", "fingerprint", "payload"}
             and type(result["result_id"]) is int and result["result_id"] > 0,
             "invalid result directory")
    _require(canonical_digest(result["payload"]) == result["fingerprint"],
             "export digest mismatch")
    return check_result_payload(result["payload"])


def load_solver_sample(path):
    with open(path, encoding="utf-8") as stream:
        source = json.load(stream)
    def rational(pair):
        if (type(pair) is not list or len(pair) != 2
                or any(type(x) is not int for x in pair) or pair[1] == 0):
            raise ValueError("sample rational pair required")
        return Fraction(*pair)
    mesh = {"nodes": {node: tuple(rational(x) for x in xyz)
                      for node, xyz in source["mesh"]["nodes"].items()},
            "faces": {face: tuple(vertices) for face, vertices in source["mesh"]["faces"].items()}}
    fields = {"node_scalars": {k: rational(v) for k, v in source["fields"]["node_scalars"].items()},
              "edge_integrals": {tuple(edge): rational(value)
                                 for edge, value in source["fields"]["edge_integrals"]},
              "face_totals": {k: rational(v) for k, v in source["fields"]["face_totals"].items()}}
    samples = [{"face": item["face"], "weights": tuple(rational(w) for w in item["weights"])}
               for item in source["samples"]]
    return mesh, fields, samples


def demo(sample_path):
    import tempfile
    from pathlib import Path
    from surface.sources.storage import ArchiveStore
    from surface.sources.catalog import SourceCatalog
    from surface.tasks.plans import PlanService
    from surface.tasks.execution import AnalysisService
    from surface.results.reading import ResultReader
    from surface.results.exporting import ResultExporter
    mesh, fields, samples = load_solver_sample(sample_path)
    with tempfile.TemporaryDirectory(prefix="surface-demo-") as directory:
        database = Path(directory) / "quality.sqlite"
        store = ArchiveStore.open(database)
        catalog = SourceCatalog(store)
        receipt = catalog.put_source("solver-day-1", mesh, fields)
        plan = PlanService(store).create_plan("quality-day-1", receipt["revision_id"], samples)
        result_id = AnalysisService(store).execute(plan)
        original = ResultReader(store).read_result(result_id)
        fields["node_scalars"]["A"] = Fraction(99)
        catalog.put_source("solver-correction", mesh, fields)
        store.close()
        reopened = ArchiveStore.open(database)
        try:
            reader = ResultReader(reopened)
            assert reader.read_result(result_id) == original
            raw = ResultExporter(reader).export_result(result_id)
        finally:
            reopened.close()
        report = check_export(raw)
        payload = original["payload"]
        expected = {"samples": ["0", "1", "4/3", "5", "4/3"],
                    "circulations": {"f1": "62/105", "f2": "37/70"}}
        actual = {"samples": [str(item["value"]) for item in payload["samples"]],
                  "circulations": {k: str(v) for k, v in payload["circulations"].items()}}
        downstream = {"boundary": str(report.boundary_total), "internal": str(report.internal_total),
                      "gap": str(report.gap_total), "frozen_revision": payload["binding"]["revision_id"]}
        downstream_expected = {"boundary": "47/42", "internal": "0", "gap": "25/14",
                               "frozen_revision": receipt["revision_id"]}
        assert actual == expected
        assert downstream == downstream_expected
        print("POINTWISE_SCENARIO " + json.dumps({
            "case_id": "v8.offline_exact", "input": {"sample": sample_path, "correction_A": "99"},
            "expected": expected, "actual": actual,
            "downstream_expected": downstream_expected, "downstream_actual": downstream,
            "code_paths": ["surface/tasks/execution.py", "surface/tasks/inputs.py",
                           "surface/fields/sampling.py", "surface/fields/circulation.py",
                           "surface/fields/balances.py", "surface/results/exporting.py",
                           "surface/results/checking.py"],
            "oracle": "independent hand fractions and offline traversal of saved solver input"
        }, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description="Offline surface quality review")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--demo", metavar="SOLVER_JSON")
    group.add_argument("--check", metavar="EXPORT_FILE")
    args = parser.parse_args()
    if args.demo:
        demo(args.demo)
    else:
        with open(args.check, "rb") as stream:
            report = check_export(stream.read())
        print(json.dumps({"faces": report.faces, "samples": report.samples,
                          "boundary_total": str(report.boundary_total),
                          "internal_total": str(report.internal_total),
                          "gap_total": str(report.gap_total)}, sort_keys=True))


if __name__ == "__main__":
    main()
