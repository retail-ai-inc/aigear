"""Opt-in small-object contract against an explicitly configured test bucket."""
from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest

from aigear.common.schema.bucket_schema import ObjectNotFound, PreconditionFailed
from aigear.db.bucket import RealGCS


@pytest.mark.live_gcs
@pytest.mark.skipif(not os.environ.get('AIGEAR_TEST_GCS_BUCKET'), reason='No authorized GCS test bucket configured')
def test_authorized_gcs_small_object_versions_copy_and_custom_time():
    bucket = RealGCS(os.environ.get('AIGEAR_TEST_GCS_PROJECT'), os.environ['AIGEAR_TEST_GCS_BUCKET'])
    operation = uuid4().hex
    prefix = 'aigear-contract-test/' + operation
    source, destination = prefix + '/source.json', prefix + '/copy.json'
    owned = {}
    try:
        info = bucket.write_bytes(source, b'{"value":1}', if_generation_match='0',
            custom_time=datetime.now(timezone.utc), metadata={'aigear_operation': operation}, content_type='application/json')
        owned[source] = info.generation
        assert bucket.read_json(source, generation=info.generation) == {'value': 1}
        with pytest.raises(PreconditionFailed): bucket.write_json(source, {'value': 2}, if_generation_match='0')
        copied = bucket.copy_blob(source, destination, source_generation=info.generation, if_generation_match='0',
            metadata={'aigear_operation': operation})
        owned[destination] = copied.generation
        assert copied.custom_time is None and copied.crc32c == info.crc32c and copied.size == info.size
        now = datetime.now(timezone.utc)
        updated = bucket.update_metadata(destination, if_generation_match=copied.generation,
            if_metageneration_match=copied.metageneration, custom_time=now)
        assert updated.generation == copied.generation and updated.custom_time == now
    finally:
        try:
            for key in [destination, source]:
                try:
                    info = bucket.get_metadata(key)
                except ObjectNotFound:
                    continue
                if info.metadata.get('aigear_operation') == operation and (key not in owned or owned[key] == info.generation):
                    bucket.delete(key, if_generation_match=info.generation)
        finally:
            bucket.close()
