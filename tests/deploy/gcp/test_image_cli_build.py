from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aigear.cli import artifacts_image as cli
from aigear.deploy.gcp import artifacts_image as images


@pytest.fixture
def docker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for filename, venv in (("Dockerfile.pl", "pl"), ("Dockerfile.ms", "ms")):
        (tmp_path / filename).write_text(f"ENV VENV_BASE=/opt/venv\nENV VENV=${{VENV_BASE}}/{venv}\n", encoding="utf-8")
    monkeypatch.setattr(images.AigearConfig, "get_config", lambda: SimpleNamespace(gcp=SimpleNamespace(location="region")))
    monkeypatch.setattr(images.AppConfig, "pipelines", lambda: {"v1": {"venv_pl": "pl", "model_service": {"venv_ms": "ms"}}})
    monkeypatch.setattr(images, "get_image_path", lambda **kwargs: "registry/ms:v1" if kwargs.get("is_service") else "registry/pl:v1")
    stream = Mock(return_value=0)
    monkeypatch.setattr(images, "run_sh_stream", stream)
    return stream


def test_all_build_reaches_docker_for_both_images(docker, monkeypatch):
    monkeypatch.setattr("sys.argv", ["aigear-image", "--create", "--all"])
    cli.docker_image()
    assert [call.args[0] for call in docker.call_args_list] == [
        ["docker", "build", "-f", "Dockerfile.pl", "-t", "registry/pl:v1", "."],
        ["docker", "build", "-f", "Dockerfile.ms", "-t", "registry/ms:v1", "."],
    ]


@pytest.mark.parametrize("service,filename", [(False, "Dockerfile.pl"), (True, "Dockerfile.ms")])
def test_explicit_build_uses_correct_dockerfile(docker, monkeypatch, service, filename, capsys):
    monkeypatch.setattr("sys.argv", ["aigear-image", "--create", "--dockerfile_path", filename, *(["--is_service"] if service else [])])
    cli.docker_image()
    assert docker.call_count == 1
    assert docker.call_args.args[0][3] == filename
    assert "operation completed" in capsys.readouterr().out


@pytest.mark.parametrize("extra", [[], ["--is_service"]])
def test_missing_build_scope_never_invokes_docker(docker, monkeypatch, extra):
    monkeypatch.setattr("sys.argv", ["aigear-image", "--create", *extra])
    with pytest.raises(SystemExit) as exc:
        cli.docker_image()
    assert exc.value.code == 2
    docker.assert_not_called()


def test_build_failure_exits_nonzero(docker, monkeypatch, capsys):
    docker.return_value = 1
    monkeypatch.setattr("sys.argv", ["aigear-image", "--create", "--dockerfile_path", "Dockerfile.pl"])
    with pytest.raises(SystemExit) as exc:
        cli.docker_image()
    assert exc.value.code == 1
    assert "operation failed" in capsys.readouterr().out


@pytest.mark.parametrize("operation,function,extra", [
    ("create", "create_artifacts_image", ["--dockerfile_path", "Dockerfile.pl"]),
    ("delete", "delete_artifacts_image", []),
    ("clear", "clear_artifacts_image", []),
    ("retag", "retag_artifacts_image", ["--src_tag", "v1", "--target_tag", "v2"]),
])
def test_image_operation_failure_exits_nonzero(monkeypatch, capsys, operation, function, extra):
    monkeypatch.setattr("sys.argv", ["aigear-image", f"--{operation}", *extra])
    action = Mock(return_value=False)
    monkeypatch.setattr(cli, function, action)
    with pytest.raises(SystemExit) as exc:
        cli.docker_image()
    assert exc.value.code == 1
    action.assert_called_once()
    assert "operation failed" in capsys.readouterr().out


@pytest.mark.parametrize("results", [(False, True), (True, False), (False, False), (True, True)])
def test_all_images_report_each_result_and_fail_if_any_operation_failed(monkeypatch, capsys, results):
    monkeypatch.setattr("sys.argv", ["aigear-image", "--create", "--all"])
    action = Mock(side_effect=results)
    monkeypatch.setattr(cli, "create_artifacts_image", action)
    if all(results):
        cli.docker_image()
    else:
        with pytest.raises(SystemExit) as exc:
            cli.docker_image()
        assert exc.value.code == 1
    assert action.call_count == 2
    output = capsys.readouterr().out
    assert output.count("operation completed") == sum(results)
    assert output.count("operation failed") == len(results) - sum(results)
