from fractions import Fraction


def validate_sample(mesh, sample):
    if type(sample) is not dict or set(sample) != {"face", "weights"}:
        raise ValueError("sample requires face and weights")
    face, weights = sample["face"], sample["weights"]
    if type(face) is not str or face not in mesh.faces:
        raise ValueError("unknown sampling face")
    if (type(weights) not in (tuple, list) or len(weights) != 3
            or any(not isinstance(w, Fraction) for w in weights)
            or any(w < 0 for w in weights) or sum(weights) != 1):
        raise ValueError("weights must be three nonnegative Fractions summing to one")
    return {"face": face, "weights": tuple(weights)}


def barycentric_position(mesh, sample):
    sample = validate_sample(mesh, sample)
    vertices = mesh.faces[sample["face"]]
    return tuple(sum((weight * mesh.nodes[node][axis]
                      for weight, node in zip(sample["weights"], vertices)),
                     Fraction(0)) for axis in range(3))
