from copy import deepcopy
from unittest.mock import Mock

import pytest

from aigear.common.schema.bucket_schema import BucketLifecycle, PreconditionFailed, StoragePermissionDenied
from aigear.infrastructure.gcp import bucket as module
from aigear.infrastructure.gcp.bucket import Bucket, merge_trash_lifecycle, trash_rule
from aigear.management.asset_errors import IntegrityError, SnapshotConflict


def test_merge_replaces_only_owned_rule_and_retains_unrelated_order_without_mutation():
    unrelated = {'action': {'type': 'Delete'}, 'condition': {'matchesPrefix': ['tmp/'], 'age': 60}}
    transition = {'action': {'type': 'SetStorageClass', 'storageClass': 'COLDLINE'}, 'condition': {'age': 90}}
    rules = [unrelated, trash_rule(10), transition]
    original = deepcopy(rules)
    updated = merge_trash_lifecycle(rules, 30)
    assert updated == [unrelated, trash_rule(30), transition]
    assert rules == original
    assert merge_trash_lifecycle(updated, 30) == updated
    assert merge_trash_lifecycle([unrelated], 30) == [unrelated, trash_rule(30)]


@pytest.mark.parametrize('condition', [
    {'age': 30}, {'matchesPrefix': [''], 'age': 30}, {'matchesPrefix': [], 'age': 30},
    {'matchesPrefix': ['trash'], 'age': 30}, {'matchesPrefix': ['trash/v1/'], 'age': 30},
    {'matchesPrefix': ['trash/', 'tmp/'], 'daysSinceCustomTime': 30},
    {'matchesPrefix': ['trash/'], 'daysSinceCustomTime': 30, 'age': 1},
    {'matchesPrefix': ['trash/'], 'daysSinceCustomTime': True},
])
def test_broad_or_ambiguous_delete_rules_are_rejected(condition):
    with pytest.raises(SnapshotConflict):
        merge_trash_lifecycle([{'action': {'type': 'Delete'}, 'condition': condition}], 30)


def test_duplicate_owned_rules_are_ambiguous():
    with pytest.raises(SnapshotConflict, match='Multiple'):
        merge_trash_lifecycle([trash_rule(10), trash_rule(20)], 30)


@pytest.mark.parametrize('rules', [None, {}, [None], [{'action': None, 'condition': {}}]])
def test_malformed_rule_lists_are_rejected(rules):
    with pytest.raises(IntegrityError): merge_trash_lifecycle(rules, 30)


def test_prefixes_are_case_sensitive_and_disjoint_rules_remain():
    rules = [{'action': {'type': 'Delete'}, 'condition': {'matchesPrefix': ['Trash/', 'assets/'], 'age': 10}}]
    assert merge_trash_lifecycle(rules, 30) == rules + [trash_rule(30)]


@pytest.mark.parametrize('days', [0, -1, True, '30', 1.5])
def test_invalid_retention_rejected_before_client_creation(monkeypatch, days):
    constructor = Mock()
    monkeypatch.setattr(module, 'RealGCS', constructor)
    with pytest.raises(ValueError): Bucket('train', 'region', 'project').ensure_trash_lifecycle(days)
    constructor.assert_not_called()


def test_legacy_none_does_not_read_or_create_lifecycle_configuration(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr(module, 'RealGCS', constructor)
    bucket = Bucket('train', 'region', 'project')
    assert bucket.ensure_trash_lifecycle(None) is None
    assert 'disabled' in bucket.trash_lifecycle_status(None)
    constructor.assert_not_called()


def test_lifecycle_update_uses_read_metageneration_and_is_idempotent(monkeypatch):
    state = BucketLifecycle('9223372036854775807', [])
    client = Mock()
    client.get_lifecycle.side_effect = lambda: state
    def update(rules, *, if_metageneration_match):
        nonlocal state
        assert if_metageneration_match == state.metageneration
        state = BucketLifecycle(str(int(state.metageneration) + 1), rules)
        return state
    client.update_lifecycle.side_effect = update
    monkeypatch.setattr(module, 'RealGCS', Mock(return_value=client))
    bucket = Bucket('train', 'region', 'project')
    assert bucket.ensure_trash_lifecycle(30).rules == [trash_rule(30)]
    bucket.ensure_trash_lifecycle(30)
    assert client.update_lifecycle.call_count == 1
    assert client.close.call_count == 2
    assert bucket.trash_lifecycle_status(30).startswith('EXISTS')
    assert bucket.trash_lifecycle_status(60).startswith('PARTIAL')
    assert client.update_lifecycle.call_count == 1


def test_metageneration_conflict_is_not_overwritten_or_retried_unconditionally(monkeypatch):
    client = Mock()
    client.get_lifecycle.return_value = BucketLifecycle('10', [])
    cause = RuntimeError('concurrent bucket metadata update')
    client.update_lifecycle.side_effect = PreconditionFailed('Bucket changed', phase='lifecycle', cause=cause)
    monkeypatch.setattr(module, 'RealGCS', Mock(return_value=client))
    with pytest.raises(PreconditionFailed) as failure:
        Bucket('train', 'region', 'project').ensure_trash_lifecycle(30)
    assert failure.value.cause is cause
    client.update_lifecycle.assert_called_once_with([trash_rule(30)], if_metageneration_match='10')
    client.close.assert_called_once()


def test_permission_error_retains_cause_and_closes_client(monkeypatch):
    client = Mock()
    cause = PermissionError('missing bucket metadata permission')
    client.get_lifecycle.side_effect = StoragePermissionDenied('Lifecycle permission denied', cause=cause)
    monkeypatch.setattr(module, 'RealGCS', Mock(return_value=client))
    with pytest.raises(StoragePermissionDenied) as failure:
        Bucket('train', 'region', 'project').ensure_trash_lifecycle(30)
    assert failure.value.cause is cause
    client.update_lifecycle.assert_not_called()
    client.close.assert_called_once()
