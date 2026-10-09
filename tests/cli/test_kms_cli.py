from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aigear.cli import kms_cli


def _args():
    return Namespace(project_id=None, location=None, keyring=None, key=None)


def test_full_cli_overrides_do_not_load_env(monkeypatch):
    loader = Mock(side_effect=FileNotFoundError)
    constructor = Mock()
    monkeypatch.setattr(kms_cli.AigearConfig, "get_config", loader)
    monkeypatch.setattr(kms_cli, "CloudKMS", constructor)
    kms_cli._build_kms(Namespace(project_id="p", location="r", keyring="ring", key="key"))
    loader.assert_not_called()
    constructor.assert_called_once_with(project_id="p", location="r", keyring_name="ring", key_name="key")


def test_partial_cli_overrides_fall_back_to_env(monkeypatch):
    monkeypatch.setattr(kms_cli.AigearConfig, "get_config", lambda: SimpleNamespace(gcp=SimpleNamespace(
        gcp_project_id="env-project", location="env-region", kms=SimpleNamespace(keyring_name="env-ring", key_name="env-key"))))
    constructor = Mock()
    monkeypatch.setattr(kms_cli, "CloudKMS", constructor)
    args = _args()
    args.key = "override-key"
    kms_cli._build_kms(args)
    constructor.assert_called_once_with(project_id="env-project", location="env-region", keyring_name="env-ring", key_name="override-key")


def test_missing_env_requires_explicit_kms_settings(monkeypatch):
    monkeypatch.setattr(kms_cli.AigearConfig, "get_config", Mock(side_effect=FileNotFoundError))
    with pytest.raises(SystemExit, match="--project-id PROJECT"):
        kms_cli._build_kms(_args())


@pytest.mark.parametrize("operation,environment", [("encrypt", "staging"), ("encrypt", "production"), ("decrypt", "production")])
def test_kms_default_paths(tmp_path, monkeypatch, operation, environment):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["aigear-kms-env", f"--{operation}", "--environment", environment])
    kms = Mock()
    monkeypatch.setattr(kms_cli, "_build_kms", lambda _args: kms)
    kms_cli.kms_env()
    ciphertext = tmp_path / "kms" / environment / f"{environment}-env.bin"
    if operation == "encrypt":
        kms.encrypt_env.assert_called_once_with(env_path=tmp_path / "env.json", output_path=ciphertext)
        assert ciphertext.parent.is_dir()
    else:
        kms.decrypt_env.assert_called_once_with(input_path=ciphertext, output_path=tmp_path / "env.json")


@pytest.mark.parametrize("operation", ["encrypt", "decrypt"])
def test_kms_custom_paths(tmp_path, monkeypatch, operation):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["aigear-kms-env", f"--{operation}", "--input", "input.bin", "--output", "custom/output.bin"])
    kms = Mock()
    monkeypatch.setattr(kms_cli, "_build_kms", lambda _args: kms)
    kms_cli.kms_env()
    kwargs = getattr(kms, f"{operation}_env").call_args.kwargs
    assert str(kwargs["output_path"]).replace("\\", "/") == "custom/output.bin"
    assert kwargs["env_path" if operation == "encrypt" else "input_path"] == "input.bin"
