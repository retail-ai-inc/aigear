"""Transactional local object store.

Objects are readable at their bucket-relative paths. Immutable recovery payloads
and version metadata live outside that tree. A durable projection journal lets
the next locked operation repair readable files after a process crash. SQLite's
OS locks serialize compare/write across processes.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager, ExitStack
from datetime import datetime
from pathlib import Path

from aigear.common.schema.bucket_schema import BucketABC, BucketLifecycle, ObjectMetadata, ObjectNotFound
from aigear.db.storage_common import atomic_file, check_version, crc32c, storage_call, utc, version
from aigear.management.asset_errors import IntegrityError, InvalidAssetPath
from aigear.management.asset_paths import relative_path, resolve_under


class _Transaction:
    def __init__(self, connection, client):
        self.connection = connection
        self.client = client
        self.created = []
        self.obsolete = []
        self.committed = False
        self.projections = {}
        self.journals = []

    def execute(self, *args):
        return self.connection.execute(*args)

    def project(self, name, previous, row):
        journal = self.client._state / (uuid.uuid4().hex + ".pending.json")
        atomic_file(journal, json.dumps({"name": name}).encode("utf-8"))
        self.journals.append(journal)
        self.projections[name] = previous
        self.client._project(name, row)


@contextmanager
def _transactions(*clients):
    # Order locks by canonical database path, including opposite-direction copies.
    unique = {str(c._database): c for c in clients}
    transactions = {}
    with ExitStack() as stack:
        try:
            for key in sorted(unique):
                connection = stack.enter_context(closing(unique[key]._connect()))
                connection.execute("BEGIN IMMEDIATE")
                transaction = _Transaction(connection, unique[key])
                transactions[key] = transaction
                unique[key]._recover(transaction)
            yield transactions
            # Commit the changed destination before releasing the read-only
            # source lock. Otherwise a source writer could enter between them.
            for transaction in sorted(transactions.values(),
                                      key=lambda item: item.connection.total_changes, reverse=True):
                transaction.connection.commit()
                transaction.committed = True
        except BaseException:
            for transaction in transactions.values():
                if not transaction.committed:
                    # Restore the old readable files while the database lock is
                    # still held; only then roll back metadata and release it.
                    for name, previous in transaction.projections.items():
                        transaction.client._project(name, previous)
                    transaction.connection.rollback()
                    for path in transaction.created:
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    for path in transaction.journals:
                        path.unlink(missing_ok=True)
            raise
        else:
            for transaction in transactions.values():
                for path in transaction.obsolete + transaction.journals:
                    # A failed cleanup is harmless: this payload is unreachable.
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass


class LocalGCSMock(BucketABC):
    def __init__(self, project_id, bucket_name):
        super().__init__(project_id, bucket_name or "gcs_mock")
        self.bucket_path = Path(bucket_name or "gcs_mock")
        self._root = self.bucket_path.resolve()
        self._private_root = self._root.parent / ".aigear-local"
        # Do not let a preexisting directory link redirect control data.
        if self._private_root.resolve() != self._private_root:
            raise InvalidAssetPath("Local control root must not be a directory link")
        digest = hashlib.sha256(os.path.normcase(str(self._root)).encode()).hexdigest()
        self._state = self._private_root / digest
        if self._state.resolve() != self._state:
            raise InvalidAssetPath("Local control store must not be a directory link")
        self._database = resolve_under(self._state, "metadata.sqlite3")
        with storage_call("connect"):
            self.bucket_path.mkdir(parents=True, exist_ok=True)
            self._state.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection, connection:
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS bucket (
                        id INTEGER PRIMARY KEY CHECK(id=1), sequence INTEGER NOT NULL,
                        metageneration INTEGER NOT NULL, lifecycle TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS objects (
                        name TEXT PRIMARY KEY, generation TEXT NOT NULL,
                        metageneration TEXT NOT NULL, size INTEGER NOT NULL,
                        crc32c TEXT NOT NULL, custom_time TEXT, metadata TEXT NOT NULL,
                        content_type TEXT, payload TEXT NOT NULL);
                """)
                connection.execute("INSERT OR IGNORE INTO bucket VALUES (1, ?, 1, '[]')",
                                   (time.time_ns(),))
            with _transactions(self):
                pass  # Recover readable files before returning a reopened store.

    def _connect(self):
        connection = sqlite3.connect(self._database, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def close(self):
        pass  # Connections belong to individual operations.

    def _key(self, name):
        name = relative_path(name)
        resolve_under(self._root, name)
        return name

    def _row(self, transaction, name):
        row = transaction.execute("SELECT * FROM objects WHERE name=?", (name,)).fetchone()
        if row is None:
            cause = FileNotFoundError(name)
            raise ObjectNotFound("Object does not exist", phase="metadata", cause=cause) from cause
        return row

    @staticmethod
    def _metadata(row):
        custom_time = datetime.fromisoformat(row["custom_time"]) if row["custom_time"] else None
        return ObjectMetadata(row["name"], row["generation"], row["metageneration"],
                              row["size"], row["crc32c"], custom_time,
                              json.loads(row["metadata"]), row["content_type"])

    def _data(self, row):
        data = (self._state / row["payload"]).read_bytes()
        if len(data) != row["size"] or crc32c(data) != row["crc32c"]:
            raise IntegrityError("Local payload failed checksum validation", phase="read")
        return data

    def _project(self, name, row):
        """Publish a complete, ordinary file; version metadata stays private."""
        target = resolve_under(self._root, name)
        if row is None:
            try:
                if not target.is_dir():
                    target.unlink(missing_ok=True)
            except NotADirectoryError:
                pass  # A failed write below an existing file never created it.
            parent = target.parent
            while parent != self._root:
                try:
                    parent.rmdir()  # Remove only empty projection directories.
                except OSError:
                    break
                parent = parent.parent
        else:
            data = self._data(row)
            if not target.is_file() or target.read_bytes() != data:
                if target.is_dir():
                    target.rmdir()  # Never remove untracked files to make room.
                atomic_file(target, data)

    def _recover(self, transaction):
        for journal in sorted(self._state.glob("*.pending.json")):
            try:
                record = journal.read_bytes()
            except FileNotFoundError:
                continue  # The just-committed writer already cleaned its journal.
            name = self._key(json.loads(record)["name"])
            row = transaction.execute("SELECT * FROM objects WHERE name=?", (name,)).fetchone()
            self._project(name, row)
            journal.unlink(missing_ok=True)

    def get_metadata(self, name):
        name = self._key(name)
        with storage_call("metadata"), _transactions(self) as transactions:
            return self._metadata(self._row(transactions[str(self._database)], name))

    def list_metadata(self, prefix=""):
        if prefix:
            self._key(prefix)
        with storage_call("list"), _transactions(self) as transactions:
            rows = transactions[str(self._database)].execute("SELECT * FROM objects ORDER BY name")
            return [self._metadata(row) for row in rows if row["name"].startswith(prefix)]

    def read_bytes(self, name, *, generation=None):
        name = self._key(name)
        generation = version(generation)
        with storage_call("read"), _transactions(self) as transactions:
            row = self._row(transactions[str(self._database)], name)
            check_version(row["generation"], generation, fixed=True)
            return self._data(row)

    def _put(self, transaction, name, data, condition, custom_time, metadata, content_type):
        row = transaction.execute("SELECT * FROM objects WHERE name=?", (name,)).fetchone()
        check_version(row["generation"] if row else "0", condition)
        if row is None and resolve_under(self._root, name).exists():
            raise IntegrityError("Unmanaged local file occupies the object path", phase="upload")
        transaction.execute("UPDATE bucket SET sequence=sequence+1 WHERE id=1")
        generation = str(transaction.execute("SELECT sequence FROM bucket").fetchone()[0])
        payload = self._state / (uuid.uuid4().hex + ".blob")
        transaction.created.append(payload)
        atomic_file(payload, data)
        transaction.execute("INSERT OR REPLACE INTO objects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (name, generation, "1", len(data), crc32c(data),
                             custom_time.isoformat() if custom_time else None,
                             json.dumps(metadata or {}), content_type or "application/octet-stream",
                             payload.name))
        transaction.project(name, row, self._row(transaction, name))
        if row:
            transaction.obsolete.append(self._state / row["payload"])
        return self._metadata(self._row(transaction, name))

    def write_bytes(self, name, data, *, if_generation_match=None,
                    custom_time=None, metadata=None, content_type=None):
        name = self._key(name)
        condition = version(if_generation_match, zero=True)
        custom_time = utc(custom_time)
        with storage_call("upload"), _transactions(self) as transactions:
            return self._put(transactions[str(self._database)], name, data, condition,
                             custom_time, metadata, content_type)

    def download(self, bucket_blob_name, local_blob_path, *, generation=None):
        target = Path(local_blob_path).resolve()
        if target.is_relative_to(self._root) or target.is_relative_to(self._private_root):
            raise InvalidAssetPath("Download cache must be outside the bucket and control roots")
        data = self.read_bytes(bucket_blob_name, generation=generation)
        with storage_call("download"):
            atomic_file(target, data)

    def upload(self, local_blob_name, bucket_blob_name, *, if_generation_match=None,
               custom_time=None, metadata=None, content_type=None):
        with storage_call("upload"):
            return self.write_bytes(bucket_blob_name, Path(local_blob_name).read_bytes(),
                                    if_generation_match=if_generation_match, custom_time=custom_time,
                                    metadata=metadata, content_type=content_type)

    def copy_blob(self, source_blob_name, destination_blob_name, *, destination_bucket=None,
                  source_generation=None, if_generation_match=None, custom_time=None, metadata=None):
        destination = self if destination_bucket is None else destination_bucket
        if not isinstance(destination, LocalGCSMock):
            raise TypeError("Copy requires two local buckets")
        source_name = self._key(source_blob_name)
        target_name = destination._key(destination_blob_name)
        source_generation = version(source_generation)
        condition = version(if_generation_match, zero=True)
        custom_time = utc(custom_time)
        with storage_call("copy"), _transactions(self, destination) as transactions:
            source = self._row(transactions[str(self._database)], source_name)
            check_version(source["generation"], source_generation, fixed=True)
            # Both stores remain locked until the destination pointer commits.
            return destination._put(transactions[str(destination._database)], target_name,
                                    self._data(source), condition, custom_time,
                                    json.loads(source["metadata"]) if metadata is None else metadata, source["content_type"])

    def delete(self, name, *, if_generation_match):
        name = self._key(name)
        condition = version(if_generation_match, optional=False)
        with storage_call("delete"), _transactions(self) as transactions:
            transaction = transactions[str(self._database)]
            row = self._row(transaction, name)
            check_version(row["generation"], condition)
            transaction.execute("DELETE FROM objects WHERE name=?", (name,))
            transaction.project(name, row, None)
            transaction.obsolete.append(self._state / row["payload"])

    def update_metadata(self, name, *, if_generation_match, if_metageneration_match,
                        custom_time=None, metadata=None):
        name = self._key(name)
        generation = version(if_generation_match, optional=False)
        metageneration = version(if_metageneration_match, optional=False)
        custom_time = utc(custom_time)
        with storage_call("patch"), _transactions(self) as transactions:
            transaction = transactions[str(self._database)]
            row = self._row(transaction, name)
            check_version(row["generation"], generation)
            check_version(row["metageneration"], metageneration)
            previous = self._metadata(row).custom_time
            if custom_time is not None and previous is not None and custom_time < previous:
                raise ValueError("Custom-Time cannot move backwards; use rewrite")
            transaction.execute("""UPDATE objects SET metageneration=?, custom_time=?, metadata=?
                                   WHERE name=?""",
                                (str(int(row["metageneration"]) + 1),
                                 (custom_time or previous).isoformat() if custom_time or previous else None,
                                 json.dumps(metadata) if metadata is not None else row["metadata"], name))
            return self._metadata(self._row(transaction, name))

    def get_lifecycle(self):
        with storage_call("lifecycle_read"), _transactions(self) as transactions:
            row = transactions[str(self._database)].execute("SELECT * FROM bucket").fetchone()
            return BucketLifecycle(str(row["metageneration"]), json.loads(row["lifecycle"]))

    def update_lifecycle(self, rules, *, if_metageneration_match):
        condition = version(if_metageneration_match, optional=False)
        encoded = json.dumps(rules, allow_nan=False)
        with storage_call("lifecycle_update"), _transactions(self) as transactions:
            transaction = transactions[str(self._database)]
            row = transaction.execute("SELECT * FROM bucket").fetchone()
            check_version(str(row["metageneration"]), condition)
            next_version = row["metageneration"] + 1
            transaction.execute("UPDATE bucket SET metageneration=?, lifecycle=? WHERE id=1",
                                (next_version, encoded))
            return BucketLifecycle(str(next_version), json.loads(encoded))
