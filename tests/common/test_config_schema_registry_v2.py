"""Additive V2 config fields must never break existing env.json parsing.

See docs/pipeline-v2-phase-a-tasks.md T4.
"""

import json

import pytest

from aigear.common.schema.config_schema import Config


def _load_template_config() -> dict:
    with open("src/aigear/template/env.sample.json", encoding="utf-8") as f:
        return json.load(f)["aigear"]


def test_template_config_still_validates_without_v2_fields():
    config = Config.model_validate(_load_template_config())

    assert config.gcp.firestore.database_id is None
    assert config.gcp.registry_v2 is None


def test_firestore_database_id_is_optional_and_settable():
    raw = _load_template_config()
    raw["gcp"]["firestore"]["database_id"] = "aigear-v2"

    config = Config.model_validate(raw)

    assert config.gcp.firestore.database_id == "aigear-v2"


def test_registry_v2_section_is_optional_and_settable():
    raw = _load_template_config()
    raw["gcp"]["registry_v2"] = {
        "asset_bucket_location": "asia-northeast1",
        "kms_trust_domain": "projects/my-project/locations/asia-northeast1/keyRings/aigear",
        "security_journal_bucket": "aigear-prod-security-journal",
    }

    config = Config.model_validate(raw)

    assert config.gcp.registry_v2 is not None
    assert config.gcp.registry_v2.asset_bucket_location == "asia-northeast1"
    assert config.gcp.registry_v2.security_journal_bucket == "aigear-prod-security-journal"


def test_registry_v2_fields_are_individually_optional():
    raw = _load_template_config()
    raw["gcp"]["registry_v2"] = {"kms_trust_domain": "trust-domain-only"}

    config = Config.model_validate(raw)

    assert config.gcp.registry_v2.kms_trust_domain == "trust-domain-only"
    assert config.gcp.registry_v2.asset_bucket_location is None
    assert config.gcp.registry_v2.security_journal_bucket is None


def _enabled_registry_v2() -> dict:
    prefix = "projects/p/locations/asia-northeast1/keyRings/r/cryptoKeys"
    return {
        "enabled": True,
        "environment_id": "production",
        "gcp_project_number": "123456789012",
        "asset_bucket_location": "asia-northeast1",
        "kms_trust_domain": "projects/p/locations/asia-northeast1/keyRings/r",
        "security_journal_bucket": "aigear-security-journal",
        "manifest_integrity_key_version": f"{prefix}/manifest/cryptoKeyVersions/1",
        "blob_location_key_version": f"{prefix}/location/cryptoKeyVersions/1",
        "occurrence_finalization_key_version": f"{prefix}/occurrence/cryptoKeyVersions/1",
        "completion_topic": "pipeline-completion",
        "completion_subscription": "pipeline-finalizer",
        "completion_publisher_service_account": "pubsub-push@p.iam.gserviceaccount.com",
        "completion_oidc_audience": "https://finalizer.example.test/completion",
    }


def test_enabled_registry_v2_requires_enabled_real_gcp_dependencies():
    raw = _load_template_config()
    raw["gcp"]["registry_v2"] = _enabled_registry_v2()
    raw["gcp"]["firestore"]["database_id"] = "aigear-prod"
    raw["gcp"]["kms"]["on"] = True
    config = Config.model_validate(raw)
    assert config.gcp.registry_v2.enabled is True


def test_enabled_registry_v2_rejects_missing_database_id():
    raw = _load_template_config()
    raw["gcp"]["registry_v2"] = _enabled_registry_v2()
    raw["gcp"]["kms"]["on"] = True
    with pytest.raises(ValueError, match="database_id"):
        Config.model_validate(raw)


def test_enabled_registry_v2_rejects_reused_attestation_key():
    raw = _load_template_config()
    registry = _enabled_registry_v2()
    registry["blob_location_key_version"] = registry["manifest_integrity_key_version"]
    raw["gcp"]["registry_v2"] = registry
    raw["gcp"]["firestore"]["database_id"] = "aigear-prod"
    raw["gcp"]["kms"]["on"] = True
    with pytest.raises(ValueError, match="must be distinct"):
        Config.model_validate(raw)
