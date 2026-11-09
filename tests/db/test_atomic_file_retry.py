from pathlib import Path

import pytest

from aigear.db import storage_common
from aigear.db.bucket import LocalGCSMock
from aigear.common.schema.bucket_schema import StoragePermissionDenied


@pytest.mark.parametrize('permanent', [False, True])
def test_windows_replace_retry_is_bounded_and_preserves_committed_content(tmp_path, monkeypatch, permanent):
    bucket = LocalGCSMock('project', tmp_path / 'bucket')
    original = bucket.write_json('manifest.json', {'value': 'old'})
    replace = storage_common.os.replace
    calls = []
    def temporarily_locked(source, destination):
        if Path(destination) == bucket.bucket_path / 'manifest.json':
            calls.append(destination)
            if permanent or len(calls) == 1:
                error = PermissionError('Windows file temporarily unavailable')
                error.winerror = 5
                raise error
        return replace(source, destination)
    monkeypatch.setattr(storage_common.os, 'replace', temporarily_locked)
    monkeypatch.setattr(storage_common.time, 'sleep', lambda _: None)
    if permanent:
        with pytest.raises(StoragePermissionDenied) as failure:
            bucket.write_json('manifest.json', {'value': 'new'}, if_generation_match=original.generation)
        assert isinstance(failure.value.cause, PermissionError)
        assert len(calls) == 6
        assert bucket.get_metadata('manifest.json') == original
        assert bucket.read_json('manifest.json') == {'value': 'old'}
    else:
        bucket.write_json('manifest.json', {'value': 'new'}, if_generation_match=original.generation)
        assert len(calls) == 2 and bucket.read_json('manifest.json') == {'value': 'new'}
    assert not list(bucket.bucket_path.glob('.aigear-*'))
