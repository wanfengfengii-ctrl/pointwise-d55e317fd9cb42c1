import hashlib
import json
from fractions import Fraction

FORMAT_VERSION = 1


def _pack(value):
    if isinstance(value, Fraction):
        return {"$fraction": [value.numerator, value.denominator]}
    if value is None or type(value) in (str, int):
        return value
    if type(value) is tuple:
        return {"$tuple": [_pack(item) for item in value]}
    if type(value) is list:
        return [_pack(item) for item in value]
    if type(value) is dict:
        pairs = [[_pack(key), _pack(item)] for key, item in value.items()]
        pairs.sort(key=lambda pair: json.dumps(pair[0], sort_keys=True,
                                               ensure_ascii=True))
        return {"$map": pairs}
    raise ValueError("unsupported exact value: " + type(value).__name__)


def _unpack(value):
    if value is None or type(value) in (str, int):
        return value
    if type(value) is list:
        return [_unpack(item) for item in value]
    if type(value) is not dict or len(value) != 1:
        raise ValueError("invalid exact encoding")
    if "$fraction" in value:
        pair = value["$fraction"]
        if (type(pair) is not list or len(pair) != 2
                or any(type(x) is not int for x in pair) or pair[1] <= 0):
            raise ValueError("invalid rational pair")
        result = Fraction(*pair)
        if [result.numerator, result.denominator] != pair:
            raise ValueError("noncanonical rational")
        return result
    if "$tuple" in value and type(value["$tuple"]) is list:
        return tuple(_unpack(item) for item in value["$tuple"])
    if "$map" in value and type(value["$map"]) is list:
        result = {}
        for pair in value["$map"]:
            if type(pair) is not list or len(pair) != 2:
                raise ValueError("invalid map entry")
            key, item = map(_unpack, pair)
            try:
                if key in result:
                    raise ValueError("duplicate identity")
                result[key] = item
            except TypeError as exc:
                raise ValueError("unhashable identity") from exc
        return result
    raise ValueError("unknown exact tag")


def _members(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError("invalid numeric constant: " + value)


def encode_payload(value):
    envelope = {"format": "surface-exact", "version": FORMAT_VERSION,
                "payload": _pack(value)}
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def decode_payload(raw):
    try:
        envelope = json.loads(raw, object_pairs_hook=_members,
                              parse_constant=_bad_constant)
        if (type(envelope) is not dict
                or set(envelope) != {"format", "version", "payload"}
                or envelope["format"] != "surface-exact"
                or type(envelope["version"]) is not int
                or envelope["version"] != FORMAT_VERSION):
            raise ValueError("unsupported archive encoding")
        return _unpack(envelope["payload"])
    except (TypeError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("corrupt exact payload") from exc


def canonical_digest(value):
    return hashlib.sha256(encode_payload(value)).hexdigest()
