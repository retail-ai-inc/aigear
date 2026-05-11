import pytest
from unittest.mock import patch, MagicMock

from aigear.common.constant import VENV_BASE_DIR
from aigear.deploy.gcp.artifacts_image import ArtifactsImage, _validate_dockerfile_venvs


def _make_artifacts_image(image_name="asia-northeast1-docker.pkg.dev/proj/repo/my-image:latest"):
    return ArtifactsImage(artifacts_image=image_name)


# ── ArtifactsImage.create_image ──────────────────────────────────────────────

@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_create_image_returns_true_on_success(mock_stream):
    mock_stream.return_value = 0
    ai = _make_artifacts_image()
    assert ai.create_image(dockerfile_path="Dockerfile.pl") is True


@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_create_image_returns_false_on_failure(mock_stream):
    mock_stream.return_value = 1
    ai = _make_artifacts_image()
    assert ai.create_image(dockerfile_path="Dockerfile.pl") is False


def test_create_image_returns_false_when_no_dockerfile():
    ai = _make_artifacts_image()
    assert ai.create_image(dockerfile_path=None) is False


@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_create_image_builds_correct_docker_command(mock_stream):
    mock_stream.return_value = 0
    ai = _make_artifacts_image(image_name="my-image:v1")
    ai.create_image(dockerfile_path="Dockerfile.pl", build_context=".")
    cmd = mock_stream.call_args[0][0]
    assert "docker" in cmd
    assert "build" in cmd
    assert "-f" in cmd
    assert "Dockerfile.pl" in cmd
    assert "-t" in cmd
    assert "my-image:v1" in cmd


# ── ArtifactsImage.image_exist_in_artifacts ──────────────────────────────────

@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_image_exist_returns_true_when_found(mock_run_sh):
    mock_run_sh.return_value = "digest: sha256:abc"
    ai = _make_artifacts_image()
    assert ai.image_exist_in_artifacts() is True


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_image_exist_returns_false_when_not_found(mock_run_sh):
    mock_run_sh.return_value = "ERROR: Image not found"
    ai = _make_artifacts_image()
    assert ai.image_exist_in_artifacts() is False


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_image_exist_returns_false_on_not_found_variant(mock_run_sh):
    mock_run_sh.return_value = "ERROR: NOT_FOUND: some resource"
    ai = _make_artifacts_image()
    assert ai.image_exist_in_artifacts() is False


# ── _validate_dockerfile_venvs ────────────────────────────────────────────────

def test_validate_raises_on_venv_base_mismatch(tmp_path):
    dockerfile = tmp_path / "Dockerfile.pl"
    dockerfile.write_text("VENV_BASE=/wrong/path\n")

    with patch("aigear.deploy.gcp.artifacts_image.AppConfig.pipelines", return_value={}):
        with pytest.raises(ValueError, match="VENV_BASE mismatch"):
            _validate_dockerfile_venvs(str(dockerfile), is_service=False)


def test_validate_raises_when_pipeline_venv_missing_from_dockerfile(tmp_path):
    dockerfile = tmp_path / "Dockerfile.pl"
    dockerfile.write_text(f"VENV_BASE={VENV_BASE_DIR}\n# no venv here\n")

    pipelines = {"v1": {"venv_pl": "my-venv"}}
    with patch("aigear.deploy.gcp.artifacts_image.AppConfig.pipelines", return_value=pipelines):
        with pytest.raises(ValueError, match="my-venv"):
            _validate_dockerfile_venvs(str(dockerfile), is_service=False)


def test_validate_passes_when_pipeline_venv_present(tmp_path):
    dockerfile = tmp_path / "Dockerfile.pl"
    dockerfile.write_text(
        f"VENV_BASE={VENV_BASE_DIR}\n"
        f"RUN ${{VENV_BASE}}/my-venv/bin/python ...\n"
    )

    pipelines = {"v1": {"venv_pl": "my-venv"}}
    with patch("aigear.deploy.gcp.artifacts_image.AppConfig.pipelines", return_value=pipelines):
        _validate_dockerfile_venvs(str(dockerfile), is_service=False)  # must not raise


def test_validate_raises_when_service_venv_missing(tmp_path):
    dockerfile = tmp_path / "Dockerfile.ms"
    dockerfile.write_text(f"VENV_BASE={VENV_BASE_DIR}\n# no ms venv\n")

    pipelines = {"v1": {"model_service": {"venv_ms": "ms-venv"}}}
    with patch("aigear.deploy.gcp.artifacts_image.AppConfig.pipelines", return_value=pipelines):
        with pytest.raises(ValueError, match="ms-venv"):
            _validate_dockerfile_venvs(str(dockerfile), is_service=True)


def test_validate_passes_when_service_venv_present(tmp_path):
    dockerfile = tmp_path / "Dockerfile.ms"
    dockerfile.write_text(
        f"VENV_BASE={VENV_BASE_DIR}\n"
        f"RUN ${{VENV_BASE}}/ms-venv/bin/python ...\n"
    )

    pipelines = {"v1": {"model_service": {"venv_ms": "ms-venv"}}}
    with patch("aigear.deploy.gcp.artifacts_image.AppConfig.pipelines", return_value=pipelines):
        _validate_dockerfile_venvs(str(dockerfile), is_service=True)  # must not raise


def test_validate_skips_non_dict_pipeline_entries(tmp_path):
    dockerfile = tmp_path / "Dockerfile.pl"
    dockerfile.write_text(f"VENV_BASE={VENV_BASE_DIR}\n")

    # Non-dict entry should be skipped without error
    pipelines = {"__comment__": "some string value"}
    with patch("aigear.deploy.gcp.artifacts_image.AppConfig.pipelines", return_value=pipelines):
        _validate_dockerfile_venvs(str(dockerfile), is_service=False)  # must not raise


def test_validate_passes_when_no_venv_configured(tmp_path):
    dockerfile = tmp_path / "Dockerfile.pl"
    dockerfile.write_text(f"VENV_BASE={VENV_BASE_DIR}\n")

    # Pipeline with no venv_pl → nothing to check
    pipelines = {"v1": {"schedule": "0 9 * * *"}}
    with patch("aigear.deploy.gcp.artifacts_image.AppConfig.pipelines", return_value=pipelines):
        _validate_dockerfile_venvs(str(dockerfile), is_service=False)  # must not raise
