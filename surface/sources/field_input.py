from fractions import Fraction
from surface.fields.bindings import FieldValues, validate_coverage


def validate_fraction_values(mapping):
    if type(mapping) is not dict:
        raise ValueError("field mapping required")
    if any(not isinstance(value, Fraction) for value in mapping.values()):
        raise ValueError("only Fraction field values are accepted")
    return dict(mapping)


def normalize_fields(mesh, record):
    names = {"node_scalars", "edge_integrals", "face_totals"}
    if type(record) is not dict or set(record) != names:
        raise ValueError("all three field mappings required")
    values = {name: validate_fraction_values(record[name]) for name in names}
    for name in ("node_scalars", "face_totals"):
        if any(type(key) is not str or not key for key in values[name]):
            raise ValueError("invalid field identity")
    for key in values["edge_integrals"]:
        if (type(key) is not tuple or len(key) != 2
                or any(type(node) is not str for node in key) or key[0] >= key[1]):
            raise ValueError("edge fields require canonical endpoint tuples")
    result = FieldValues(**values)
    validate_coverage(mesh, result)
    return result
