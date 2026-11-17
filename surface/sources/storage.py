import sqlite3
from contextlib import contextmanager


class ArchiveStore:
    def __init__(self, connection):
        self.connection = connection
        self.fault = None

    @classmethod
    def open(cls, path=":memory:"):
        connection = sqlite3.connect(path, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        store = cls(connection)
        try:
            store.initialize()
        except BaseException:
            connection.close()
            raise
        return store

    def initialize(self):
        from surface.sources.revisions import RevisionRepository
        from surface.tasks.plans import PlanService
        from surface.results.repository import ResultRepository
        from surface.tasks.receipts import ReceiptRepository
        with self.transaction():
            tables = {row[0] for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if "archive_format" not in tables:
                if tables:
                    raise ValueError("unrecognized archive without format metadata")
                self.connection.execute(
                    "CREATE TABLE archive_format (singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL)")
                self.connection.execute("INSERT INTO archive_format VALUES(1,1)")
            try:
                versions = self.connection.execute(
                    "SELECT singleton,version FROM archive_format").fetchall()
            except sqlite3.DatabaseError as exc:
                raise ValueError("damaged format metadata") from exc
            if versions != [(1, 1)]:
                raise ValueError("unsupported archive version")
            for owner in (RevisionRepository, PlanService, ResultRepository, ReceiptRepository):
                owner.initialize(self)

    @contextmanager
    def transaction(self):
        if self.connection.in_transaction:
            raise RuntimeError("nested archive transaction")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def require_transaction(self):
        if not self.connection.in_transaction:
            raise RuntimeError("business write requires caller transaction")

    def checkpoint(self, stage):
        if self.fault is not None:
            self.fault(stage)

    def close(self):
        self.connection.close()
