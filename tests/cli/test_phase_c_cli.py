from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from aigear.cli.asset import asset_cli
from aigear.management.v2.fake_registry import FakeRegistryV2
from aigear.management.v2.identifiers import TypedId
from aigear.management.v2.import_reservation import (
    compute_import_idempotency_key_hash,
)
from aigear.management.v2.records.release import ReleasePhase
from tests.management.v2.records.test_import_operation import _operation as _import_operation
from tests.management.v2.records.test_release import _operation as _release_operation
from tests.management.v2.test_release_query import _KEY, _NOW, _registry
from tests.management.v2.test_release_registry import _state


def test_import_status_has_stable_json_contract(capsys):
    registry = FakeRegistryV2()
    key = "import-request-1"
    operation = _import_operation(
        idempotency_key_hash=compute_import_idempotency_key_hash(key)
    )
    registry.put_import_operation(operation)

    asset_cli(
        [
            "import",
            "status",
            "--pipeline-version",
            "pipeline-v2",
            "--idempotency-key",
            key,
        ],
        v2_registry=registry,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "2.0"
    assert payload["kind"] == "import_status"
    assert payload["record"]["operation_id"] == "import-1"


def test_release_show_returns_all_three_facts_champion_and_operation(capsys):
    registry = FakeRegistryV2()
    desired = TypedId.from_bare("11" * 32)
    observed = TypedId.from_bare("22" * 32)
    traffic = TypedId.from_bare("33" * 32)
    operation = replace(
        _release_operation(),
        operation_id="release-active",
        target_release_id=desired,
        phase=ReleasePhase.SWITCHING_TRAFFIC,
    )
    state = replace(
        _state(),
        desired_release_id=desired,
        desired_revision=1,
        observed_release_id=observed,
        observed_evidence_revision=1,
        traffic_release_id=traffic,
        traffic_k8s_resource_version="42",
        champion_release_id=traffic,
        active_operation_id=operation.operation_id,
        active_operation_phase=operation.phase,
    )
    registry.put_release_operation(operation)
    registry.put_service_release_state(state)

    asset_cli(
        [
            "release",
            "show",
            "--pipeline-version",
            "pipeline-v2",
            "--service-name",
            "predictor",
        ],
        v2_registry=registry,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "release_status"
    assert payload["state"]["desired_release_id"] == desired.typed
    assert payload["state"]["observed_release_id"] == observed.typed
    assert payload["state"]["traffic_release_id"] == traffic.typed
    assert payload["state"]["champion_release_id"] == traffic.typed
    assert payload["operation"]["operation_id"] == "release-active"


def test_release_history_exposes_signed_cursor_without_offset(capsys):
    registry, _asset_id = _registry()

    asset_cli(
        [
            "release",
            "history",
            "--pipeline-version",
            "pipeline-v2",
            "--service-name",
            "predictor",
            "--page-size",
            "2",
        ],
        v2_registry=registry,
        page_token_signing_key=_KEY,
        now=_NOW,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "2.0"
    assert payload["kind"] == "release_history"
    assert len(payload["items"]) == 2
    assert payload["page"]["next_page_token"]


def test_release_impact_accepts_typed_asset_id(capsys):
    registry, asset_id = _registry()

    asset_cli(
        [
            "release",
            "impact",
            "--pipeline-version",
            "pipeline-v2",
            "--asset-version-id",
            asset_id.typed,
            "--page-size",
            "1",
        ],
        v2_registry=registry,
        page_token_signing_key=_KEY,
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "release_impact"
    assert payload["items"][0]["asset_version_ids"] == [asset_id.typed]


def test_phase_c_query_failure_is_json_and_exit_two(capsys):
    with pytest.raises(SystemExit) as excinfo:
        asset_cli(
            [
                "release",
                "show",
                "--pipeline-version",
                "pipeline-v2",
                "--service-name",
                "missing",
            ],
            v2_registry=FakeRegistryV2(),
        )

    assert excinfo.value.code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "schema_version": "2.0",
        "kind": "error",
        "error": {
            "code": "invalid_request",
            "message": "service release state was not found",
        },
    }
