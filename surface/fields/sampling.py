from dataclasses import dataclass
from fractions import Fraction
from surface.mesh.barycentric import validate_sample, barycentric_position


@dataclass(frozen=True)
class SampleValue:
    face: str
    weights: tuple
    position: tuple
    value: Fraction

    def to_record(self):
        return {"face": self.face, "weights": self.weights,
                "position": self.position, "value": self.value}


def interpolate_sample(bundle, sample):
    sample = validate_sample(bundle.mesh, sample)
    vertices = bundle.mesh.faces[sample["face"]]
    value = sum((weight * bundle.values.node_scalars[node]
                 for weight, node in zip(sample["weights"], vertices)), Fraction(0))
    return SampleValue(sample["face"], sample["weights"],
                       barycentric_position(bundle.mesh, sample), value)


def sample_queue(bundle, samples):
    return [interpolate_sample(bundle, sample) for sample in samples]
