from aigear.common.run_id import derive_run_id


def test_derive_run_id_is_stable_for_same_input():
    assert derive_run_id("demo", "v1", "2026-05-21T00:00:00Z") == "b95f18fed7fa7ad6"


def test_derive_run_id_allows_missing_project_name():
    assert derive_run_id(None, "v1", "2026-05-21T00:00:00Z") == "b788424d09792d3b"
