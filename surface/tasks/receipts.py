from dataclasses import dataclass


@dataclass(frozen=True)
class CompletionReceipt:
    plan_id: int
    result_id: int


class ReceiptRepository:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def initialize(store):
        store.connection.execute(
            "CREATE TABLE IF NOT EXISTS completion_receipts (plan_id INTEGER PRIMARY KEY REFERENCES analysis_plans(id), result_id INTEGER NOT NULL UNIQUE REFERENCES analysis_results(id))")

    def find(self, plan_id):
        row = self.store.connection.execute(
            "SELECT result_id FROM completion_receipts WHERE plan_id=?", (plan_id,)).fetchone()
        return None if row is None else CompletionReceipt(plan_id, row[0])

    def record_completion(self, plan_id, result_id):
        self.store.require_transaction()
        row = self.store.connection.execute(
            "SELECT plan_id FROM analysis_results WHERE id=?", (result_id,)).fetchone()
        if row != (plan_id,):
            raise ValueError("completion/result plan mismatch")
        self.store.connection.execute("INSERT INTO completion_receipts VALUES(?,?)",
                                      (plan_id, result_id))
