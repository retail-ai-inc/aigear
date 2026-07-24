"""Additive V2 config fields must never break existing env.json parsing.

See docs/pipeline-v2-phase-a-tasks.md T4.
"""

import json

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
