import json
import pytest
from unittest.mock import patch, call

from aigear.infrastructure.gcp.kms import CloudKMS


def _make_kms():
    return CloudKMS(
        project_id="my-project",
        location="asia-northeast1",
        keyring_name="my-keyring",
        key_name="my-key",
    )


# ── CloudKMS.describe_keyring ─────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_describe_keyring_returns_true_when_exists(mock_run_sh):
    mock_run_sh.return_value = "name: projects/my-project/locations/.../keyrings/my-keyring"
    kms = _make_kms()
    assert kms.describe_keyring() is True


@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_describe_keyring_returns_false_when_not_found(mock_run_sh):
    mock_run_sh.return_value = "ERROR: NOT_FOUND"
    kms = _make_kms()
    assert kms.describe_keyring() is False


@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_describe_keyring_returns_false_when_output_empty(mock_run_sh):
    mock_run_sh.return_value = ""
    kms = _make_kms()
    assert kms.describe_keyring() is False


# ── CloudKMS.describe_key ─────────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_describe_key_returns_true_when_exists(mock_run_sh):
    mock_run_sh.return_value = "name: .../cryptoKeys/my-key"
    kms = _make_kms()
    assert kms.describe_key() is True


@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_describe_key_returns_false_when_not_found(mock_run_sh):
    mock_run_sh.return_value = "ERROR: NOT_FOUND"
    kms = _make_kms()
    assert kms.describe_key() is False


# ── CloudKMS.describe_enabled_key_version ────────────────────────────────────

@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_describe_enabled_version_returns_true_when_enabled_exists(mock_run_sh):
    mock_run_sh.return_value = json.dumps([{"name": "versions/1", "state": "ENABLED"}])
    kms = _make_kms()
    assert kms.describe_enabled_key_version() is True


@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_describe_enabled_version_returns_false_when_list_empty(mock_run_sh):
    mock_run_sh.return_value = json.dumps([])
    kms = _make_kms()
    assert kms.describe_enabled_key_version() is False


@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_describe_enabled_version_returns_false_on_invalid_json(mock_run_sh):
    mock_run_sh.return_value = "not valid json"
    kms = _make_kms()
    assert kms.describe_enabled_key_version() is False


# ── CloudKMS.encrypt_env / decrypt_env ───────────────────────────────────────

@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_encrypt_env_raises_when_plaintext_file_not_found(mock_run_sh, tmp_path):
    kms = _make_kms()
    with pytest.raises(FileNotFoundError):
        kms.encrypt_env(env_path=tmp_path / "nonexistent.json")


@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_decrypt_env_raises_when_ciphertext_file_not_found(mock_run_sh, tmp_path):
    kms = _make_kms()
    with pytest.raises(FileNotFoundError):
        kms.decrypt_env(input_path=tmp_path / "nonexistent.bin")


@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_encrypt_env_calls_encrypt_with_correct_paths(mock_run_sh, tmp_path):
    env_file = tmp_path / "env.json"
    env_file.write_text("{}")
    output_file = tmp_path / "out.bin"
    kms = _make_kms()
    kms.encrypt_env(env_path=env_file, output_path=output_file)
    cmd = mock_run_sh.call_args[0][0]
    assert f"--plaintext-file={env_file}" in cmd
    assert f"--ciphertext-file={output_file}" in cmd


@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_decrypt_env_calls_decrypt_with_correct_paths(mock_run_sh, tmp_path):
    cipher_file = tmp_path / "out.bin"
    cipher_file.write_bytes(b"\x00\x01")
    output_file = tmp_path / "env.json"
    kms = _make_kms()
    kms.decrypt_env(input_path=cipher_file, output_path=output_file)
    cmd = mock_run_sh.call_args[0][0]
    assert f"--ciphertext-file={cipher_file}" in cmd
    assert f"--plaintext-file={output_file}" in cmd


# ── CloudKMS.enable_primary_key_version ──────────────────────────────────────

@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_enable_primary_key_version_raises_when_state_destroyed(mock_run_sh):
    mock_run_sh.return_value = json.dumps({
        "primary": {"name": "projects/p/keys/k/versions/1", "state": "DESTROYED"}
    })
    kms = _make_kms()
    with pytest.raises(RuntimeError, match="DESTROYED"):
        kms.enable_primary_key_version()


@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_enable_primary_key_version_skips_enable_when_already_enabled(mock_run_sh):
    mock_run_sh.return_value = json.dumps({
        "primary": {"name": "projects/p/keys/k/versions/1", "state": "ENABLED"}
    })
    kms = _make_kms()
    kms.enable_primary_key_version()
    # Only the describe call; no enable call
    assert mock_run_sh.call_count == 1


@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_enable_primary_key_version_raises_when_no_primary(mock_run_sh):
    mock_run_sh.return_value = json.dumps({})
    kms = _make_kms()
    with pytest.raises(RuntimeError, match="No primary version"):
        kms.enable_primary_key_version()


@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_enable_primary_key_version_calls_enable_when_disabled(mock_run_sh):
    mock_run_sh.return_value = json.dumps({
        "primary": {"name": "projects/p/keys/k/versions/2", "state": "DISABLED"}
    })
    kms = _make_kms()
    kms.enable_primary_key_version()
    # First call: describe, second call: enable
    assert mock_run_sh.call_count == 2
    enable_cmd = mock_run_sh.call_args_list[1][0][0]
    assert "enable" in enable_cmd
    assert "2" in enable_cmd


# ── CloudKMS.delete ───────────────────────────────────────────────────────────

@patch("aigear.infrastructure.gcp.kms.run_sh")
def test_delete_destroys_enabled_and_disabled_versions(mock_run_sh):
    versions = [
        {"name": "projects/p/keys/k/versions/1", "state": "ENABLED"},
        {"name": "projects/p/keys/k/versions/2", "state": "DISABLED"},
        {"name": "projects/p/keys/k/versions/3", "state": "DESTROYED"},
    ]
    # First call: list versions; subsequent calls: destroy
    mock_run_sh.side_effect = [json.dumps(versions), "", ""]
    kms = _make_kms()
    kms.delete()
    # 1 list call + 2 destroy calls (ENABLED + DISABLED); DESTROYED is skipped
    assert mock_run_sh.call_count == 3
