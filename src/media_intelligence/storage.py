"""PostgreSQL persistence. One transaction protects every complete write operation."""
from contextlib import contextmanager
import json
from sqlalchemy import create_engine, text
from .core import canonical, key, parse


class Store:
    def __init__(self, database_url):
        self.engine = create_engine(database_url, pool_pre_ping=True)

    @contextmanager
    def transaction(self, write=False):
        with self.engine.begin() as connection:
            if write:
                # Serialize this small instance's writes, including retries and version checks.
                connection.execute(text("SELECT pg_advisory_xact_lock(73649218)"))
            yield Transaction(connection)


class Transaction:
    def __init__(self, connection):
        self.connection = connection

    def objects(self):
        rows = self.connection.execute(text("SELECT body FROM publications ORDER BY object_id, version"))
        return [parse(row[0]) for row in rows]

    def insert(self, objects):
        for obj in objects:
            self.connection.execute(text("""
                INSERT INTO publications (object_id, version, object_type, created_by, body)
                VALUES (:id, :version, :type, :org, CAST(:body AS jsonb))
                ON CONFLICT (object_id, version) DO NOTHING
            """), {"id": obj.id, "version": key(obj)[1], "type": obj.type,
                   "org": obj.created_by, "body": canonical(obj)})

    def retry(self, request_key):
        row = self.connection.execute(text("SELECT fingerprint, response FROM request_retries WHERE request_key=:key"), {"key": request_key}).first()
        return row

    def remember(self, request_key, fingerprint, response):
        self.connection.execute(text("""INSERT INTO request_retries (request_key, fingerprint, response)
            VALUES (:key, :fingerprint, CAST(:response AS jsonb))"""),
            {"key": request_key, "fingerprint": fingerprint, "response": json.dumps(response)})

    def log_import(self, package_id, sender, inserted):
        self.connection.execute(text("""INSERT INTO import_records (package_id, sender, inserted)
            VALUES (:id, :sender, :inserted)"""), {"id": str(package_id), "sender": sender, "inserted": inserted})
