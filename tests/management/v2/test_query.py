from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.query import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    PageToken,
    QueryError,
    list_assets,
    list_run_outputs,
)
from aigear.management.v2.records.asset_version import (
    AssetComponent,
    AssetVersionRecord,
    LifecycleState,
    ProducerSpec,
    TrustState,
    compute_asset_version_id,
)
from aigear.management.v2.records.occurrence import (
    OccurrenceRecord,
    OccurrenceStatus,
    compute_committed_output_key,
    compute_metrics_digest,
    compute_occurrence_id,
    compute_resolved_inputs_digest,
)

_FP = TypedId.from_bare("aa" * 32)
_IMAGE_DIGEST = TypedId.from_bare("11" * 32)
_CODE_DIGEST = TypedId.from_bare("22" * 32)
_CONFIG_DIGEST = TypedId.from_bare("33" * 32)
_SCHEMA_CONTRACT_DIGEST = TypedId.from_bare("55" * 32)
_RUNTIME_CONTRACT_DIGEST = TypedId.from_bare("66" * 32)
_ATTESTATION = TypedId.from_bare("77" * 32)


def _producer_spec() -> ProducerSpec:
    return ProducerSpec(
        source_commit="abc123", image_digest=_IMAGE_DIGEST, code_digest=_CODE_DIGEST,
        config_digest=_CONFIG_DIGEST,
    )


def _asset_version(
    blob_hex: str, *, asset_type: str = "model", name: str = "weights",
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE,
    trust_state: TrustState = TrustState.QUARANTINED, created_at=None,
) -> AssetVersionRecord:
    components = (
        AssetComponent(role="model", blob_id=TypedId.from_bare(blob_hex), logical_name="weights", media_type="application/octet-stream"),
    )
    manifest = {
        "environment_id": "test-env", "environment_fingerprint": _FP.typed,
        "asset_type": asset_type, "name": name,
        "components": [c.to_manifest_dict() for c in components], "input_bindings": [],
        "producer_spec": _producer_spec().to_manifest_dict(),
        "schema_contract_digest": _SCHEMA_CONTRACT_DIGEST.typed,
        "runtime_contract_digest": _RUNTIME_CONTRACT_DIGEST.typed, "policy_version": "policy-v1",
    }
    asset_version_id = compute_asset_version_id(manifest)
    return AssetVersionRecord(
        schema_version="2.0", environment_id="test-env", environment_fingerprint=_FP,
        asset_version_id=asset_version_id, asset_type=asset_type, name=name,
        manifest_digest=asset_version_id, record_revision=1, components=components,
        input_bindings=(), producer_spec=_producer_spec(),
        schema_contract_digest=_SCHEMA_CONTRACT_DIGEST, runtime_contract_digest=_RUNTIME_CONTRACT_DIGEST,
        lifecycle_state=lifecycle_state, trust_state=trust_state, policy_version="policy-v1",
        manifest_integrity_attestation_ref=_ATTESTATION, created_at=created_at,
    )


def _occurrence(
    run_id: str, step_name: str, output_name: str, *, attempt_no: int = 1,
    committed_at=None, status: OccurrenceStatus = OccurrenceStatus.COMMITTED,
) -> OccurrenceRecord:
    occurrence_id = compute_occurrence_id(run_id, step_name, attempt_no, output_name)
    committed_output_key = compute_committed_output_key(run_id, step_name, output_name)
    kwargs = {}
    if status not in (OccurrenceStatus.PROVISIONAL, OccurrenceStatus.ABORTED):
        kwargs = dict(
            asset_version_id=TypedId.from_bare("99" * 32), asset_type="model", asset_name="weights",
            finalization_attestation_ref=_ATTESTATION,
        )
    return OccurrenceRecord(
        schema_version="2.0", environment_fingerprint=_FP, occurrence_id=occurrence_id,
        run_id=run_id, step_name=step_name, attempt_no=attempt_no, fencing_token=1,
        output_name=output_name, committed_output_key=committed_output_key,
        resolved_input_bindings=(), resolved_inputs_digest=compute_resolved_inputs_digest(()),
        metrics={}, metrics_digest=compute_metrics_digest({}), status=status,
        operation_id="op-1", committed_at=committed_at, **kwargs,
    )


def _ts(seconds: int) -> str:
    return datetime(2026, 1, 1, 0, 0, seconds, tzinfo=timezone.utc).isoformat()


def test_list_assets_pushes_limit_cutoff_and_cursor_to_bounded_backend():
    records = (
        _asset_version("01" * 32, created_at=_ts(1)),
        _asset_version("02" * 32, created_at=_ts(2)),
    )

    class Backend:
        def __init__(self):
            self.calls = []

        def query_asset_versions(self, **kwargs):
            self.calls.append(kwargs)
            return records if kwargs["cursor"] is None else records[1:]

    backend = Backend()
    first = list_assets(
        backend,
        schema_version="2.0",
        asset_type="model",
        name="weights",
        page_size=1,
        now=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
    )
    assert len(first.items) == 1
    assert backend.calls[0]["limit"] == 2
    assert backend.calls[0]["cursor"] is None
    assert backend.calls[0]["page_cutoff"].startswith("2026-01-01T00:01")

    list_assets(
        backend,
        schema_version="2.0",
        asset_type="model",
        name="weights",
        page_size=1,
        page_token=first.next_page_token,
        now=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    assert backend.calls[1]["cursor"][0] == records[0].created_at
    assert backend.calls[1]["cursor"][1] == records[0].asset_version_id.typed


# ── list_assets ───────────────────────────────────────────────────────────────


def test_list_assets_returns_matching_versions_ordered_by_created_at():
    registry = FakeRegistryV2()
    v1 = _asset_version("aa" * 32, created_at=_ts(1))
    v2 = _asset_version("bb" * 32, created_at=_ts(2))
    registry.put_asset_version(v2)
    registry.put_asset_version(v1)

    page = list_assets(
        registry, schema_version="2.0", asset_type="model", name="weights",
        now=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
    )

    assert [v.asset_version_id for v in page.items] == [v1.asset_version_id, v2.asset_version_id]
    assert page.next_page_token is None


def test_list_assets_filters_by_lifecycle_and_trust_state():
    registry = FakeRegistryV2()
    active = _asset_version("aa" * 32, lifecycle_state=LifecycleState.ACTIVE, created_at=_ts(1))
    archived = _asset_version("bb" * 32, lifecycle_state=LifecycleState.ARCHIVED, created_at=_ts(2))
    registry.put_asset_version(active)
    registry.put_asset_version(archived)

    page = list_assets(
        registry, schema_version="2.0", asset_type="model", name="weights",
        lifecycle_state=LifecycleState.ACTIVE, now=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
    )

    assert [v.asset_version_id for v in page.items] == [active.asset_version_id]


def test_list_assets_excludes_records_from_a_different_asset_type_or_name():
    registry = FakeRegistryV2()
    registry.put_asset_version(_asset_version("aa" * 32, created_at=_ts(1)))
    registry.put_asset_version(_asset_version("bb" * 32, name="other", created_at=_ts(2)))
    registry.put_asset_version(_asset_version("cc" * 32, asset_type="dataset", created_at=_ts(3)))

    page = list_assets(
        registry, schema_version="2.0", asset_type="model", name="weights",
        now=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
    )

    assert len(page.items) == 1


def test_list_assets_excludes_records_with_no_created_at():
    registry = FakeRegistryV2()
    registry.put_asset_version(_asset_version("aa" * 32, created_at=None))

    page = list_assets(
        registry, schema_version="2.0", asset_type="model", name="weights",
        now=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
    )

    assert page.items == ()


def test_list_assets_paginates_and_the_next_page_continues_from_the_cursor():
    registry = FakeRegistryV2()
    versions = [_asset_version(f"{i:02x}" * 32, created_at=_ts(i)) for i in range(1, 6)]
    for v in versions:
        registry.put_asset_version(v)
    now = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)

    first = list_assets(registry, schema_version="2.0", asset_type="model", name="weights", page_size=2, now=now)
    assert [v.asset_version_id for v in first.items] == [versions[0].asset_version_id, versions[1].asset_version_id]
    assert first.next_page_token is not None

    second = list_assets(
        registry, schema_version="2.0", asset_type="model", name="weights", page_size=2,
        page_token=first.next_page_token, now=now,
    )
    assert [v.asset_version_id for v in second.items] == [versions[2].asset_version_id, versions[3].asset_version_id]
    assert second.next_page_token is not None

    third = list_assets(
        registry, schema_version="2.0", asset_type="model", name="weights", page_size=2,
        page_token=second.next_page_token, now=now,
    )
    assert [v.asset_version_id for v in third.items] == [versions[4].asset_version_id]
    assert third.next_page_token is None


def test_list_assets_page_cutoff_excludes_records_created_after_the_first_page():
    registry = FakeRegistryV2()
    v1 = _asset_version("aa" * 32, created_at=_ts(1))
    v2 = _asset_version("bb" * 32, created_at=_ts(2))
    registry.put_asset_version(v1)
    registry.put_asset_version(v2)
    now = datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc)

    first = list_assets(
        registry, schema_version="2.0", asset_type="model", name="weights", page_size=1, now=now,
    )
    assert [v.asset_version_id for v in first.items] == [v1.asset_version_id]
    assert first.next_page_token is not None

    # A record whose created_at falls after the cutoff fixed by the first
    # page must never surface on a later page of the *same* scan, even
    # though it now exists in the registry and its cursor key would
    # otherwise sort within the remaining range.
    late = _asset_version("cc" * 32, created_at=_ts(9))
    registry.put_asset_version(late)

    second = list_assets(
        registry, schema_version="2.0", asset_type="model", name="weights", page_size=5,
        page_token=first.next_page_token, now=now,
    )
    assert [v.asset_version_id for v in second.items] == [v2.asset_version_id]
    assert second.next_page_token is None


def test_list_assets_rejects_page_token_from_a_different_filter():
    registry = FakeRegistryV2()
    registry.put_asset_version(_asset_version("aa" * 32, created_at=_ts(1)))
    registry.put_asset_version(_asset_version("bb" * 32, created_at=_ts(2)))
    now = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)

    page = list_assets(registry, schema_version="2.0", asset_type="model", name="weights", page_size=1, now=now)

    with pytest.raises(QueryError, match="filter_digest"):
        list_assets(
            registry, schema_version="2.0", asset_type="model", name="other-name", page_size=1,
            page_token=page.next_page_token, now=now,
        )


def test_list_assets_rejects_malformed_page_token():
    registry = FakeRegistryV2()
    with pytest.raises(QueryError, match="malformed page_token"):
        list_assets(
            registry, schema_version="2.0", asset_type="model", name="weights",
            page_token="not-a-valid-token", now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize("page_size", [0, -1, MAX_PAGE_SIZE + 1])
def test_list_assets_rejects_invalid_page_size(page_size):
    registry = FakeRegistryV2()
    with pytest.raises(QueryError, match="page_size"):
        list_assets(
            registry, schema_version="2.0", asset_type="model", name="weights", page_size=page_size,
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_default_and_max_page_size_constants():
    assert DEFAULT_PAGE_SIZE == 100
    assert MAX_PAGE_SIZE == 500


# ── list_run_outputs ─────────────────────────────────────────────────────────


def test_list_run_outputs_returns_only_committed_occurrences_for_the_run():
    registry = FakeRegistryV2()
    committed = _occurrence("run-1", "train", "model", committed_at=_ts(1))
    provisional = _occurrence("run-1", "train", "metrics", status=OccurrenceStatus.PROVISIONAL, attempt_no=2)
    other_run = _occurrence("run-2", "train", "model", committed_at=_ts(1))
    registry.put_occurrence(committed)
    registry.put_occurrence(provisional)
    registry.put_occurrence(other_run)

    page = list_run_outputs(
        registry, schema_version="2.0", run_id="run-1", now=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
    )

    assert [o.occurrence_id for o in page.items] == [committed.occurrence_id]


def test_list_run_outputs_filters_by_step_name():
    registry = FakeRegistryV2()
    prep = _occurrence("run-1", "prep", "features", committed_at=_ts(1))
    train = _occurrence("run-1", "train", "model", committed_at=_ts(2))
    registry.put_occurrence(prep)
    registry.put_occurrence(train)

    page = list_run_outputs(
        registry, schema_version="2.0", run_id="run-1", step_name="train",
        now=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
    )

    assert [o.occurrence_id for o in page.items] == [train.occurrence_id]


def test_list_run_outputs_orders_by_committed_at():
    registry = FakeRegistryV2()
    later = _occurrence("run-1", "train", "model", committed_at=_ts(9))
    earlier = _occurrence("run-1", "prep", "features", committed_at=_ts(1))
    registry.put_occurrence(later)
    registry.put_occurrence(earlier)

    page = list_run_outputs(
        registry, schema_version="2.0", run_id="run-1", now=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
    )

    assert [o.occurrence_id for o in page.items] == [earlier.occurrence_id, later.occurrence_id]


def test_list_run_outputs_paginates():
    registry = FakeRegistryV2()
    step_names = ["step-a", "step-b", "step-c"]
    occurrences = [
        _occurrence("run-1", step_name, "out", committed_at=_ts(i + 1))
        for i, step_name in enumerate(step_names)
    ]
    for occ in occurrences:
        registry.put_occurrence(occ)
    now = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)

    first = list_run_outputs(registry, schema_version="2.0", run_id="run-1", page_size=2, now=now)
    assert [o.occurrence_id for o in first.items] == [occurrences[0].occurrence_id, occurrences[1].occurrence_id]
    assert first.next_page_token is not None

    second = list_run_outputs(
        registry, schema_version="2.0", run_id="run-1", page_size=2, page_token=first.next_page_token, now=now,
    )
    assert [o.occurrence_id for o in second.items] == [occurrences[2].occurrence_id]
    assert second.next_page_token is None


def test_list_run_outputs_rejects_page_token_with_mismatched_database_id():
    registry = FakeRegistryV2()
    occurrences = [
        _occurrence("run-1", f"step-{i}", "out", committed_at=_ts(i + 1)) for i in range(3)
    ]
    for occ in occurrences:
        registry.put_occurrence(occ)
    now = datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc)
    page = list_run_outputs(registry, schema_version="2.0", run_id="run-1", page_size=1, now=now)

    with pytest.raises(QueryError, match="database_id"):
        list_run_outputs(
            registry, schema_version="2.0", run_id="run-1", page_size=1,
            page_token=page.next_page_token, database_id="other-db", now=now,
        )


# ── PageToken ─────────────────────────────────────────────────────────────────


def test_page_token_round_trips_through_encode_decode():
    token = PageToken(
        schema_version="2.0", filter_digest="deadbeef", order="asset_list.v1:(created_at,asset_version_id)",
        database_id="(default)", page_cutoff=_ts(5), cursor=(_ts(3), "sha256:aa"),
    )
    decoded = PageToken.decode(token.encode())
    assert decoded == token


def test_page_token_rejects_empty_cursor_component():
    with pytest.raises(QueryError, match="cursor"):
        PageToken(
            schema_version="2.0", filter_digest="deadbeef", order="o", database_id="(default)",
            page_cutoff=_ts(1), cursor=("", "sha256:aa"),
        )


def test_page_token_rejects_invalid_page_cutoff():
    with pytest.raises(QueryError, match="page_cutoff"):
        PageToken(
            schema_version="2.0", filter_digest="deadbeef", order="o", database_id="(default)",
            page_cutoff="not-a-timestamp", cursor=(_ts(1), "sha256:aa"),
        )
