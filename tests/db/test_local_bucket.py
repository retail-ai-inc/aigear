import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from aigear.common.schema.bucket_schema import StoragePermissionDenied
from aigear.db.bucket import LocalGCSMock
from aigear.management.asset_errors import IntegrityError, InvalidAssetPath


WORKER = r'''
import os, sys
from aigear.db.bucket import LocalGCSMock
from aigear.common.schema.bucket_schema import PreconditionFailed
client = LocalGCSMock("p", sys.argv[1])
print("ready", flush=True)
sys.stdin.readline()
try:
    operation = sys.argv[3]
    if operation == "write":
        client.write_json("manifest.json", {"pid": os.getpid()}, if_generation_match=sys.argv[2])
    elif operation == "patch":
        client.update_metadata("manifest.json", if_generation_match=sys.argv[2],
                               if_metageneration_match="1", metadata={"pid": str(os.getpid())})
    else:
        client.update_lifecycle([], if_metageneration_match="1")
    print("won", flush=True)
except PreconditionFailed:
    print("lost", flush=True)
'''


@pytest.mark.parametrize("operation,create_only", [
    ("write", False), ("write", True), ("patch", False), ("lifecycle", False),
])
def test_independent_processes_compare_and_commit_once(tmp_path, operation, create_only):
    root = tmp_path / "bucket"
    client = LocalGCSMock("p", root)
    generation = "0" if create_only else client.write_json("manifest.json", {"initial": True}).generation
    processes = [subprocess.Popen([sys.executable, "-c", WORKER, str(root), generation, operation],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True) for _ in range(2)]
    try:
        for process in processes:
            assert process.stdout.readline().strip() == "ready"
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        results = [process.communicate(timeout=30) for process in processes]
        assert all(process.returncode == 0 for process in processes), results
        assert sorted(stdout.strip() for stdout, _ in results) == ["lost", "won"]
        if operation == "write":
            assert client.get_metadata("manifest.json").metageneration == "1"
            assert "pid" in client.read_json("manifest.json")
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


CRASH_WORKER = r'''
import os, sys
from aigear.db.bucket import LocalGCSMock
import aigear.db.local_bucket as local
import aigear.db.storage_common as common
client = LocalGCSMock("p", sys.argv[1])
phase = sys.argv[2]
if phase == "before_replace":
    common.os.replace = lambda *args: os._exit(73)
elif phase == "after_replace":
    original = local.atomic_file
    def crash(*args):
        original(*args)
        os._exit(73)
    local.atomic_file = crash
elif phase == "after_metadata":
    original = local._Transaction.execute
    def crash(self, sql, *args):
        result = original(self, sql, *args)
        if sql.startswith("INSERT OR REPLACE INTO objects"):
            os._exit(73)
        return result
    local._Transaction.execute = crash
elif phase == "after_projection":
    original = client._project
    def crash(*args):
        original(*args)
        os._exit(73)
    client._project = crash
elif phase == "after_commit":
    import sqlite3
    class Connection(sqlite3.Connection):
        def commit(self):
            super().commit()
            os._exit(73)
    original = client._connect
    def connect():
        connection = sqlite3.connect(client._database, factory=Connection)
        connection.row_factory = sqlite3.Row
        return connection
    client._connect = connect
client.write_json("manifest.json", {"committed": "new"}, if_generation_match=sys.argv[3])
'''


@pytest.mark.parametrize("phase", ["before_replace", "after_replace", "after_metadata", "after_projection", "after_commit"])
@pytest.mark.parametrize("existing", [False, True])
def test_process_crash_never_exposes_partial_object(tmp_path, phase, existing):
    client = LocalGCSMock("p", tmp_path / "bucket")
    old = client.write_json("manifest.json", {"committed": "old"}) if existing else None
    result = subprocess.run([sys.executable, "-c", CRASH_WORKER, str(client.bucket_path), phase,
                             old.generation if old else "0"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 73, result.stderr
    reopened = LocalGCSMock("p", client.bucket_path)
    if phase == "after_commit":
        assert reopened.read_json("manifest.json") == {"committed": "new"}
        assert json.loads((client.bucket_path / "manifest.json").read_bytes()) == {"committed": "new"}
        assert len(reopened.list_metadata()) == 1
    elif old:
        assert reopened.get_metadata("manifest.json") == old
        assert reopened.read_json("manifest.json") == {"committed": "old"}
        assert json.loads((client.bucket_path / "manifest.json").read_bytes()) == {"committed": "old"}
    else:
        assert reopened.list_metadata() == []
        assert not (client.bucket_path / "manifest.json").exists()
    # Unreachable crash debris must not prevent a valid CAS retry.
    generation = reopened.get_metadata("manifest.json").generation if reopened.list_metadata() else "0"
    reopened.write_json("manifest.json", {"retry": True}, if_generation_match=generation)
    assert reopened.read_json("manifest.json") == {"retry": True}
    assert json.loads((client.bucket_path / "manifest.json").read_bytes()) == {"retry": True}


def test_atomic_replacement_failure_preserves_old_object(tmp_path, monkeypatch):
    import aigear.db.storage_common as common
    client = LocalGCSMock("p", tmp_path / "bucket")
    old = client.write_json("manifest.json", {"old": True})
    cause = PermissionError("blocked")
    def fail(*args):
        raise cause
    monkeypatch.setattr(common.os, "replace", fail)
    with pytest.raises(StoragePermissionDenied) as failure:
        client.write_json("manifest.json", {"new": True}, if_generation_match=old.generation)
    assert failure.value.cause is cause
    assert client.get_metadata("manifest.json") == old
    assert client.read_json("manifest.json") == {"old": True}


def test_metadata_and_payload_integrity_survive_reopen(tmp_path):
    client = LocalGCSMock("p", tmp_path / "bucket")
    info = client.write_bytes("object", b"data")
    payload = next(client._state.glob("*.blob"))
    os.utime(payload, (1, 1))
    reopened = LocalGCSMock("p", client.bucket_path)
    assert reopened.get_metadata("object") == info
    assert reopened.read_bytes("object", generation=info.generation) == b"data"
    payload.write_bytes(b"bad!")
    with pytest.raises(IntegrityError):
        reopened.read_bytes("object")


def test_bucket_control_and_cache_namespaces_are_separate(tmp_path):
    client = LocalGCSMock("p", tmp_path / "bucket")
    client.write_json("manifest.json", {})
    assert json.loads((client.bucket_path / "manifest.json").read_bytes()) == {}
    assert [path.name for path in client.bucket_path.iterdir()] == ["manifest.json"]
    assert [info.name for info in client.list_metadata()] == ["manifest.json"]
    for path in [client.bucket_path / "cache", client._state / "cache"]:
        with pytest.raises(InvalidAssetPath):
            client.download("manifest.json", path)
    assert not (client.bucket_path / "cache").exists()


def test_lifecycle_eligibility_uses_custom_time_not_file_age(tmp_path, monkeypatch):
    now = datetime(2026, 10, 23, 9, tzinfo=timezone.utc)
    client = LocalGCSMock("p", tmp_path / "bucket")
    monkeypatch.setattr(client, "_clock", lambda: now)
    old = client.write_bytes("trash/old", b"old")
    client.write_bytes("running/file", b"running", custom_time=now - timedelta(days=60))
    lifecycle = client.get_lifecycle()
    client.update_lifecycle([{"action": {"type": "Delete"}, "condition": {
        "matchesPrefix": ["trash/"], "daysSinceCustomTime": 30}}],
        if_metageneration_match=lifecycle.metageneration)
    assert client.lifecycle_candidates() == []
    retired = client.update_metadata("trash/old", if_generation_match=old.generation,
                                     if_metageneration_match=old.metageneration, custom_time=now)
    assert client.lifecycle_candidates() == []
    now += timedelta(days=29)
    assert client.lifecycle_candidates() == []
    now = datetime(2026, 11, 22, 0, tzinfo=timezone.utc)
    assert client.lifecycle_candidates() == []
    now = datetime(2026, 11, 22, 9, tzinfo=timezone.utc)
    assert client.lifecycle_candidates() == [retired]
    # Eligibility is read only; no cleanup service starts.
    assert client.read_bytes("trash/old") == b"old"
    copied = client.copy_blob("trash/old", "trash/reset", if_generation_match=0)
    assert copied.custom_time is None
    assert [item.name for item in client.lifecycle_candidates()] == ["trash/old"]


def test_cross_bucket_commit_keeps_source_locked_until_destination_commits(tmp_path, monkeypatch):
    import sqlite3
    source = LocalGCSMock("p", tmp_path / "a-source")
    destination = LocalGCSMock("p", tmp_path / "z-destination")
    if str(source._database) > str(destination._database):
        source, destination = destination, source
    info = source.write_bytes("file", b"pinned")
    probe = r'''
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1], timeout=0)
try:
    connection.execute("BEGIN IMMEDIATE")
    print("unlocked")
except sqlite3.OperationalError:
    print("locked")
finally:
    connection.close()
'''
    class Connection(sqlite3.Connection):
        def commit(self):
            result = subprocess.run([sys.executable, "-c", probe, str(source._database)],
                                    capture_output=True, text=True, timeout=15)
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == "locked"
            super().commit()
    def connect():
        connection = sqlite3.connect(destination._database, factory=Connection)
        connection.row_factory = sqlite3.Row
        return connection
    monkeypatch.setattr(destination, "_connect", connect)
    copied = source.copy_blob("file", "copied", destination_bucket=destination,
                              source_generation=info.generation, if_generation_match=0)
    monkeypatch.undo()
    assert destination.read_bytes("copied", generation=copied.generation) == b"pinned"
    source.write_bytes("file", b"after copy", if_generation_match=info.generation)


def test_legacy_disk_files_are_preserved_without_fabricating_versions(tmp_path):
    root = tmp_path / "bucket"
    root.mkdir()
    legacy = root / "legacy.bin"
    legacy.write_bytes(b"old layout")
    client = LocalGCSMock("p", root)
    assert client.list_metadata() == []
    client.write_bytes("managed.bin", b"new layout")
    assert legacy.read_bytes() == b"old layout"
    assert [item.name for item in client.list_metadata()] == ["managed.bin"]


def test_objects_are_readable_under_original_names_after_upload_copy_and_delete(tmp_path):
    client = LocalGCSMock("p", tmp_path / "bucket")
    first = client.write_json("running/v1/manifest.json", {"message": "可直接查看"})
    manifest = client.bucket_path / "running/v1/manifest.json"
    assert json.loads(manifest.read_text(encoding="utf-8")) == {"message": "可直接查看"}
    model = client.write_bytes("running/v1/training/model.bin", b"model")
    assert (client.bucket_path / model.name).read_bytes() == b"model"
    copied = client.copy_blob(model.name, "assets/v1/training/model.bin", source_generation=model.generation,
                              if_generation_match="0")
    assert (client.bucket_path / copied.name).read_bytes() == b"model"
    client.write_json(first.name, {"message": "updated"}, if_generation_match=first.generation)
    assert json.loads(manifest.read_bytes()) == {"message": "updated"}
    client.delete(copied.name, if_generation_match=copied.generation)
    assert not (client.bucket_path / copied.name).exists()
    assert not list(client._state.glob("*.pending.json"))


def test_failed_write_restores_readable_file_and_metadata(tmp_path, monkeypatch):
    import aigear.db.local_bucket as local
    client = LocalGCSMock("p", tmp_path / "bucket")
    old = client.write_json("manifest.json", {"committed": "old"})
    project = local._Transaction.project
    def fail_after_projection(transaction, *args):
        project(transaction, *args)
        raise RuntimeError("interrupted before commit")
    monkeypatch.setattr(local._Transaction, "project", fail_after_projection)
    with pytest.raises(RuntimeError, match="interrupted"):
        client.write_json("manifest.json", {"committed": "new"}, if_generation_match=old.generation)
    assert client.get_metadata("manifest.json") == old
    assert client.read_json("manifest.json") == {"committed": "old"}
    assert json.loads((client.bucket_path / "manifest.json").read_bytes()) == {"committed": "old"}


@pytest.mark.parametrize("commit", [False, True])
def test_interrupted_delete_recovers_readable_file_from_committed_metadata(tmp_path, commit):
    client = LocalGCSMock("p", tmp_path / "bucket")
    old = client.write_json("manifest.json", {"committed": True})
    worker = r'''
import os, sqlite3, sys
from aigear.db.bucket import LocalGCSMock
client = LocalGCSMock("p", sys.argv[1])
if sys.argv[3] == "True":
    class Connection(sqlite3.Connection):
        def commit(self):
            super().commit()
            os._exit(73)
    def connect():
        connection = sqlite3.connect(client._database, factory=Connection)
        connection.row_factory = sqlite3.Row
        return connection
    client._connect = connect
else:
    original = client._project
    def crash(*args):
        original(*args)
        os._exit(73)
    client._project = crash
client.delete("manifest.json", if_generation_match=sys.argv[2])
'''
    result = subprocess.run([sys.executable, "-c", worker, str(client.bucket_path), old.generation, str(commit)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 73, result.stderr
    reopened = LocalGCSMock("p", client.bucket_path)
    file = client.bucket_path / "manifest.json"
    if commit:
        assert not file.exists()
        assert reopened.list_metadata() == []
    else:
        assert json.loads(file.read_bytes()) == {"committed": True}
        assert reopened.get_metadata("manifest.json") == old


def test_failed_write_below_file_does_not_poison_recovery(tmp_path):
    from aigear.common.schema.bucket_schema import StorageError
    client = LocalGCSMock("p", tmp_path / "bucket")
    old = client.write_bytes("file", b"original")
    with pytest.raises(StorageError):
        client.write_bytes("file/child.bin", b"invalid local layout")
    reopened = LocalGCSMock("p", client.bucket_path)
    assert reopened.get_metadata("file") == old
    assert (client.bucket_path / "file").read_bytes() == b"original"
    assert reopened.read_bytes("file") == b"original"
    assert not list(client._state.glob("*.pending.json"))
