import pytest

from aigear.common.schema.bucket_schema import PreconditionFailed
from aigear.infrastructure.gcp import bucket as module
from aigear.infrastructure.gcp.bucket import Bucket, trash_rule


def test_infra_sync_and_status_use_backend_metageneration_contract(stores, monkeypatch):
    source, _ = stores
    monkeypatch.setattr(module, 'RealGCS', lambda *args: source)
    initial = source.get_lifecycle()
    unrelated = {'action': {'type': 'Delete'}, 'condition': {'matchesPrefix': ['tmp/'], 'age': 90}}
    source.update_lifecycle([unrelated], if_metageneration_match=initial.metageneration)
    infra = Bucket('train', 'region', 'project')
    result = infra.ensure_trash_lifecycle(30)
    assert result.rules == [unrelated, trash_rule(30)]
    assert infra.ensure_trash_lifecycle(30).metageneration == result.metageneration
    assert infra.trash_lifecycle_status(30).startswith('EXISTS')
    assert infra.trash_lifecycle_status(60).startswith('PARTIAL')


def test_infra_sync_preserves_competing_rules_on_real_backend_cas_failure(stores, monkeypatch):
    source, _ = stores
    monkeypatch.setattr(module, 'RealGCS', lambda *args: source)
    original = source.update_lifecycle
    foreign = {'action': {'type': 'Delete'}, 'condition': {'matchesPrefix': ['foreign/'], 'age': 90}}
    def racing(rules, *, if_metageneration_match):
        original([foreign], if_metageneration_match=source.get_lifecycle().metageneration)
        return original(rules, if_metageneration_match=if_metageneration_match)
    monkeypatch.setattr(source, 'update_lifecycle', racing)
    with pytest.raises(PreconditionFailed): Bucket('train', 'region', 'project').ensure_trash_lifecycle(30)
    assert source.get_lifecycle().rules == [foreign]
