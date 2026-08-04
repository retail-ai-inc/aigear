import json

from aigear.common.schema.config_schema import Config


def _load_template_config() -> dict:
    with open("src/aigear/template/env.sample.json", encoding="utf-8") as f:
        return json.load(f)["aigear"]


def test_gcp_firestore_config_is_readable_from_template():
    config = Config.model_validate(_load_template_config())

    assert config.gcp.firestore.on is True
