import pytest
from unittest.mock import patch

from aigear.common.constant import VENV_BASE_DIR
from aigear.deploy.gcp.artifacts_image import LocalImage, _validate_dockerfile_venvs

IMAGE_PATH = "asia-northeast1-docker.pkg.dev/proj/repo/my-image:latest"
IMAGE_NAME = "asia-northeast1-docker.pkg.dev/proj/repo/my-image"


def _make_local() -> LocalImage:
    return LocalImage(image_path=IMAGE_PATH)


# ── LocalImage.build ─────────────────────────────────────────────────────────

@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_local_build_returns_true_on_success(mock_stream):
    mock_stream.return_value = 0
    assert _make_local().build(dockerfile_path="Dockerfile.pl") is True


@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_local_build_returns_false_on_failure(mock_stream):
    mock_stream.return_value = 1
    assert _make_local().build(dockerfile_path="Dockerfile.pl") is False


def test_local_build_returns_false_when_no_dockerfile():
    assert _make_local().build(dockerfile_path=None) is False


@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_local_build_correct_command(mock_stream):
    mock_stream.return_value = 0
    _make_local().build(dockerfile_path="Dockerfile.pl", build_context=".")
    cmd = mock_stream.call_args[0][0]
    assert cmd == ["docker", "build", "-f", "Dockerfile.pl", "-t", IMAGE_PATH, "."]


# ── LocalImage.tag ────────────────────────────────────────────────────────────

@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_local_tag_returns_true_on_success(mock_stream):
    mock_stream.return_value = 0
    assert _make_local().tag(src_tag="v1.0", target_tag="latest") is True


@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_local_tag_returns_false_on_failure(mock_stream):
    mock_stream.return_value = 1
    assert _make_local().tag(src_tag="v1.0", target_tag="latest") is False


@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_local_tag_correct_command(mock_stream):
    mock_stream.return_value = 0
    _make_local().tag(src_tag="v1.0", target_tag="latest")
    cmd = mock_stream.call_args[0][0]
    assert cmd == [
        "docker", "tag",
        f"{IMAGE_NAME}:v1.0",
        f"{IMAGE_NAME}:latest",
    ]


# ── LocalImage.remove ─────────────────────────────────────────────────────────

@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_local_remove_returns_true_on_success(mock_stream):
    mock_stream.return_value = 0
    assert _make_local().remove() is True


@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_local_remove_returns_false_on_failure(mock_stream):
    mock_stream.return_value = 1
    assert _make_local().remove() is False


@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_local_remove_correct_command(mock_stream):
    mock_stream.return_value = 0
    _make_local().remove()
    cmd = mock_stream.call_args[0][0]
    assert cmd == ["docker", "rmi", IMAGE_PATH]


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


# ── LocalImage.prune ──────────────────────────────────────────────────────────

DOCKER_IMAGES_OUTPUT = (
    "v3\t2024-03-01 10:00:00 +0000 UTC\n"
    "v2\t2024-02-01 10:00:00 +0000 UTC\n"
    "v1\t2024-01-01 10:00:00 +0000 UTC\n"
)


@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_local_prune_deletes_oldest_beyond_keep(mock_run_sh, mock_stream):
    mock_run_sh.return_value = DOCKER_IMAGES_OUTPUT
    mock_stream.return_value = 0
    deleted = _make_local().prune(keep=2)
    assert deleted == ["v1"]
    mock_stream.assert_called_once_with(["docker", "rmi", f"{IMAGE_NAME}:v1"])


@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_local_prune_keeps_all_when_keep_exceeds_count(mock_run_sh, mock_stream):
    mock_run_sh.return_value = DOCKER_IMAGES_OUTPUT
    deleted = _make_local().prune(keep=10)
    assert deleted == []
    mock_stream.assert_not_called()


@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_local_prune_keep_1_deletes_all_but_newest(mock_run_sh, mock_stream):
    mock_run_sh.return_value = DOCKER_IMAGES_OUTPUT
    mock_stream.return_value = 0
    deleted = _make_local().prune(keep=1)
    assert deleted == ["v2", "v1"]


@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_local_prune_skips_failed_deletes(mock_run_sh, mock_stream):
    mock_run_sh.return_value = DOCKER_IMAGES_OUTPUT
    mock_stream.return_value = 1  # simulate rmi failure
    deleted = _make_local().prune(keep=1)
    assert deleted == []


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_local_prune_list_command(mock_run_sh):
    mock_run_sh.return_value = ""
    _make_local().prune(keep=1)
    list_cmd = mock_run_sh.call_args[0][0]
    assert list_cmd == [
        "docker", "images", "--format", "{{.Tag}}\t{{.CreatedAt}}", IMAGE_NAME
    ]
