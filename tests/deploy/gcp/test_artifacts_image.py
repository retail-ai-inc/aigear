import pytest
from unittest.mock import MagicMock, patch

from aigear.common.constant import VENV_BASE_DIR
from aigear.deploy.gcp.artifacts_image import (
    LocalImage,
    RegistryImage,
    create_artifacts_image,
    delete_artifacts_image,
    retag_artifacts_image,
    prune_artifacts_image,
    _validate_dockerfile_venvs,
)

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


def _make_registry() -> RegistryImage:
    return RegistryImage(image_path=IMAGE_PATH)


# ── RegistryImage.configure_auth ─────────────────────────────────────────────

@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_configure_auth_command(mock_run_sh):
    _make_registry().configure_auth("asia-northeast1")
    cmd = mock_run_sh.call_args[0][0]
    assert cmd == [
        "gcloud", "auth", "configure-docker",
        "asia-northeast1-docker.pkg.dev", "--quiet",
    ]


# ── RegistryImage.push ────────────────────────────────────────────────────────

@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_registry_push_returns_true_on_success(mock_stream):
    mock_stream.return_value = 0
    assert _make_registry().push() is True


@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_registry_push_returns_false_on_failure(mock_stream):
    mock_stream.return_value = 1
    assert _make_registry().push() is False


@patch("aigear.deploy.gcp.artifacts_image.run_sh_stream")
def test_registry_push_correct_command(mock_stream):
    mock_stream.return_value = 0
    _make_registry().push()
    assert mock_stream.call_args[0][0] == ["docker", "push", IMAGE_PATH]


# ── RegistryImage.exists ──────────────────────────────────────────────────────

@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_exists_true_when_found(mock_run_sh):
    mock_run_sh.return_value = "digest: sha256:abc"
    assert _make_registry().exists() is True


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_exists_false_when_not_found(mock_run_sh):
    mock_run_sh.return_value = "ERROR: Image not found"
    assert _make_registry().exists() is False


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_exists_false_on_not_found_variant(mock_run_sh):
    mock_run_sh.return_value = "ERROR: NOT_FOUND: some resource"
    assert _make_registry().exists() is False


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_exists_correct_command(mock_run_sh):
    mock_run_sh.return_value = "digest: sha256:abc"
    _make_registry().exists()
    cmd = mock_run_sh.call_args[0][0]
    assert cmd == [
        "gcloud", "artifacts", "docker", "images", "describe", IMAGE_PATH
    ]


# ── RegistryImage.delete ──────────────────────────────────────────────────────

@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_delete_returns_true_on_success(mock_run_sh):
    mock_run_sh.return_value = "Deleted."
    assert _make_registry().delete() is True


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_delete_returns_false_on_error(mock_run_sh):
    mock_run_sh.return_value = "ERROR: Image not found"
    assert _make_registry().delete() is False


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_delete_correct_command(mock_run_sh):
    mock_run_sh.return_value = "Deleted."
    _make_registry().delete()
    cmd = mock_run_sh.call_args[0][0]
    assert cmd == [
        "gcloud", "artifacts", "docker", "images", "delete",
        IMAGE_PATH, "--delete-tags", "--quiet",
    ]


# ── RegistryImage.retag ───────────────────────────────────────────────────────

@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_retag_returns_true_on_success(mock_run_sh):
    mock_run_sh.return_value = "Created tag."
    assert _make_registry().retag(src_tag="v1.0", target_tag="latest") is True


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_retag_returns_false_on_error(mock_run_sh):
    mock_run_sh.return_value = "ERROR: tag not found"
    assert _make_registry().retag(src_tag="v1.0", target_tag="latest") is False


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_retag_correct_command(mock_run_sh):
    mock_run_sh.return_value = "Created tag."
    _make_registry().retag(src_tag="v1.0", target_tag="latest")
    cmd = mock_run_sh.call_args[0][0]
    assert cmd == [
        "gcloud", "artifacts", "docker", "tags", "add",
        f"{IMAGE_NAME}:v1.0",
        f"{IMAGE_NAME}:latest",
    ]


# ── RegistryImage.prune ───────────────────────────────────────────────────────

GCLOUD_TAGS_OUTPUT = "v3\nv2\nv1\n"  # sorted newest-first by gcloud --sort-by=~createTime


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_prune_deletes_oldest_beyond_keep(mock_run_sh):
    mock_run_sh.side_effect = [
        GCLOUD_TAGS_OUTPUT,  # list call
        "Deleted.",           # delete v1
    ]
    deleted = _make_registry().prune(keep=2)
    assert deleted == ["v1"]


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_prune_keeps_all_when_keep_exceeds_count(mock_run_sh):
    mock_run_sh.return_value = GCLOUD_TAGS_OUTPUT
    deleted = _make_registry().prune(keep=10)
    assert deleted == []
    mock_run_sh.assert_called_once()  # only the list call


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_prune_keep_1_deletes_two(mock_run_sh):
    mock_run_sh.side_effect = [
        GCLOUD_TAGS_OUTPUT,
        "Deleted.",
        "Deleted.",
    ]
    deleted = _make_registry().prune(keep=1)
    assert deleted == ["v2", "v1"]


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_prune_skips_failed_deletes(mock_run_sh):
    # keep=1, 3 tags (v3,v2,v1) → delete v2 (fails) and v1 (succeeds)
    mock_run_sh.side_effect = [GCLOUD_TAGS_OUTPUT, "ERROR: not found", "Deleted."]
    deleted = _make_registry().prune(keep=1)
    assert deleted == ["v1"]  # v2 failed but v1 still deleted


@patch("aigear.deploy.gcp.artifacts_image.run_sh")
def test_registry_prune_list_command(mock_run_sh):
    mock_run_sh.return_value = ""
    _make_registry().prune(keep=1)
    list_cmd = mock_run_sh.call_args_list[0][0][0]
    assert list_cmd == [
        "gcloud", "artifacts", "docker", "images", "list",
        IMAGE_NAME,
        "--include-tags",
        "--sort-by=~createTime",
        "--format=value(tags)",
    ]


# ── top-level function helpers ────────────────────────────────────────────────

def _patch_image_path(is_service=False):
    return patch(
        "aigear.deploy.gcp.artifacts_image.get_image_path",
        return_value=IMAGE_PATH,
    )


def _patch_config():
    cfg = MagicMock()
    cfg.gcp.location = "asia-northeast1"
    return patch("aigear.deploy.gcp.artifacts_image.AigearConfig.get_config", return_value=cfg)


# ── create_artifacts_image ────────────────────────────────────────────────────

def _patch_validate():
    return patch("aigear.deploy.gcp.artifacts_image._validate_dockerfile_venvs")


def test_create_local_only_does_not_call_registry_push():
    with _patch_config(), _patch_image_path(), _patch_validate():
        with patch.object(LocalImage, "build", return_value=True) as mock_build, \
             patch.object(RegistryImage, "push") as mock_push:
            result = create_artifacts_image(dockerfile_path="Dockerfile.pl", is_build=True, is_push=False)
    assert result is True
    mock_build.assert_called_once()
    mock_push.assert_not_called()


def test_create_fails_fast_when_local_build_fails():
    with _patch_config(), _patch_image_path(), _patch_validate():
        with patch.object(LocalImage, "build", return_value=False), \
             patch.object(RegistryImage, "push") as mock_push:
            result = create_artifacts_image(dockerfile_path="Dockerfile.pl", is_build=True, is_push=True)
    assert result is False
    mock_push.assert_not_called()


def test_create_and_push_calls_both():
    with _patch_config(), _patch_image_path(), _patch_validate():
        with patch.object(LocalImage, "build", return_value=True), \
             patch.object(RegistryImage, "configure_auth"), \
             patch.object(RegistryImage, "push", return_value=True) as mock_push:
            result = create_artifacts_image(dockerfile_path="Dockerfile.pl", is_build=True, is_push=True)
    assert result is True
    mock_push.assert_called_once()


# ── delete_artifacts_image ────────────────────────────────────────────────────

def test_delete_local_only_does_not_call_registry_delete():
    with _patch_config(), _patch_image_path():
        with patch.object(LocalImage, "remove", return_value=True), \
             patch.object(RegistryImage, "delete") as mock_del:
            result = delete_artifacts_image(is_push=False)
    assert result is True
    mock_del.assert_not_called()


def test_delete_fails_fast_when_local_fails():
    with _patch_config(), _patch_image_path():
        with patch.object(LocalImage, "remove", return_value=False), \
             patch.object(RegistryImage, "delete") as mock_del:
            result = delete_artifacts_image(is_push=True)
    assert result is False
    mock_del.assert_not_called()


def test_delete_and_push_calls_both():
    with _patch_config(), _patch_image_path():
        with patch.object(LocalImage, "remove", return_value=True), \
             patch.object(RegistryImage, "configure_auth"), \
             patch.object(RegistryImage, "delete", return_value=True) as mock_del:
            result = delete_artifacts_image(is_push=True)
    assert result is True
    mock_del.assert_called_once()


# ── retag_artifacts_image ─────────────────────────────────────────────────────

def test_retag_local_only():
    with _patch_config(), _patch_image_path():
        with patch.object(LocalImage, "tag", return_value=True) as mock_tag, \
             patch.object(RegistryImage, "retag") as mock_retag:
            result = retag_artifacts_image(src_tag="v1.0", target_tag="latest", is_push=False)
    assert result is True
    mock_tag.assert_called_once_with(src_tag="v1.0", target_tag="latest")
    mock_retag.assert_not_called()


def test_retag_fails_fast_when_local_fails():
    with _patch_config(), _patch_image_path():
        with patch.object(LocalImage, "tag", return_value=False), \
             patch.object(RegistryImage, "retag") as mock_retag:
            result = retag_artifacts_image(src_tag="v1.0", target_tag="latest", is_push=True)
    assert result is False
    mock_retag.assert_not_called()


def test_retag_and_push_calls_both():
    with _patch_config(), _patch_image_path():
        with patch.object(LocalImage, "tag", return_value=True), \
             patch.object(RegistryImage, "configure_auth"), \
             patch.object(RegistryImage, "retag", return_value=True) as mock_retag:
            result = retag_artifacts_image(src_tag="v1.0", target_tag="latest", is_push=True)
    assert result is True
    mock_retag.assert_called_once_with(src_tag="v1.0", target_tag="latest")


# ── prune_artifacts_image ─────────────────────────────────────────────────────

def test_prune_local_only():
    with _patch_config(), _patch_image_path():
        with patch.object(LocalImage, "prune", return_value=["v1"]) as mock_prune, \
             patch.object(RegistryImage, "prune") as mock_reg_prune:
            result = prune_artifacts_image(keep=2, is_push=False)
    assert result is True
    mock_prune.assert_called_once_with(keep=2)
    mock_reg_prune.assert_not_called()


def test_prune_and_push_calls_both():
    with _patch_config(), _patch_image_path():
        with patch.object(LocalImage, "prune", return_value=[]), \
             patch.object(RegistryImage, "configure_auth"), \
             patch.object(RegistryImage, "prune", return_value=["v1"]) as mock_reg_prune:
            result = prune_artifacts_image(keep=2, is_push=True)
    assert result is True
    mock_reg_prune.assert_called_once_with(keep=2)
