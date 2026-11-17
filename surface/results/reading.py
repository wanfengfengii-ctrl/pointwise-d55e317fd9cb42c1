from surface.results.repository import ResultRepository


class ResultReader:
    def __init__(self, store):
        self.store = store
        self.repository = ResultRepository(store)

    def read_result(self, result_id):
        return self.repository.read(result_id).to_record()
