from surface.results.codec import encode_payload


def build_export_record(record):
    return {"export_kind": "surface-quality-record", "export_version": 1,
            "result": record}


class ResultExporter:
    def __init__(self, reader):
        self.reader = reader

    def export_result(self, result_id):
        record = self.reader.read_result(result_id)
        self.reader.store.checkpoint("export_before_encode")
        return encode_payload(build_export_record(record))
