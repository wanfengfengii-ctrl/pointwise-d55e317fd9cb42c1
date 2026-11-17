from dataclasses import dataclass
from fractions import Fraction
from surface.mesh.boundary import boundary_terms, internal_terms, summed_face_chain


@dataclass(frozen=True)
class BalanceSummary:
    record: dict

    def to_record(self):
        from copy import deepcopy
        return deepcopy(self.record)


def face_total_gaps(circulations, totals):
    if set(circulations) != set(totals):
        raise ValueError("circulation and total identities differ")
    return {face: circulations[face] - totals[face] for face in sorted(totals)}


def build_balance(bundle, circulations):
    totals = bundle.values.face_totals
    gaps = face_total_gaps(circulations, totals)
    def ledger(chain):
        return [{"edge": edge, "coefficient": coefficient,
                 "value": coefficient * bundle.values.edge_integrals[edge]}
                for edge, coefficient in sorted(chain.items())]
    boundary = ledger(boundary_terms(bundle.mesh))
    internal = ledger(internal_terms(bundle.mesh))
    boundary_total = sum((item["value"] for item in boundary), Fraction(0))
    internal_total = sum((item["value"] for item in internal), Fraction(0))
    circulation_total = sum(circulations.values(), Fraction(0))
    chain_total = sum((coefficient * bundle.values.edge_integrals[edge]
                       for edge, coefficient in summed_face_chain(bundle.mesh).items()),
                      Fraction(0))
    if circulation_total != boundary_total + internal_total or chain_total != circulation_total:
        raise ValueError("face-chain accounting invariant failed")
    face_total = sum(totals.values(), Fraction(0))
    return BalanceSummary({"face_gaps": gaps, "boundary_terms": boundary,
                           "internal_terms": internal,
                           "boundary_total": boundary_total,
                           "internal_total": internal_total,
                           "circulation_total": circulation_total,
                           "face_total": face_total,
                           "gap_total": circulation_total - face_total})
