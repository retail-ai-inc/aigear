from aigear.deploy.gcp.run_logs import extract_log_fields


def test_extract_log_fields_from_json_payload():
    entry = {"jsonPayload": {"run_id": "abc", "log_source": "cloud_function"}}
    assert extract_log_fields(entry)["run_id"] == "abc"


def test_extract_log_fields_from_text_payload_json():
    entry = {"textPayload": '{"run_id":"abc","run_started_at_utc":"2026-05-21T00:00:00Z"}'}
    assert extract_log_fields(entry)["run_id"] == "abc"


def test_extract_log_fields_from_text_payload_regex():
    entry = {"textPayload": 'noise {"run_id": "abc123"} tail'}
    assert extract_log_fields(entry)["run_id"] == "abc123"
