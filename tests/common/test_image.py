from types import SimpleNamespace

import pytest

from aigear.common import image


@pytest.fixture
def artifacts(monkeypatch):
    artifacts = SimpleNamespace(ms_image_name=None, pl_image_name=None, repository_name="repo", image_tag=None)
    monkeypatch.setattr(image.AigearConfig, "get_config", lambda: SimpleNamespace(
        gcp=SimpleNamespace(location="region", gcp_project_id="project", artifacts=artifacts)))
    return artifacts


@pytest.mark.parametrize("service,configured,project,expected", [
    (False, "configured", "my_project", "configured"),
    (True, "configured-ms", "my_project", "configured-ms"),
    (False, None, "my_project", "my-project"),
    (True, None, "my_project", "my-project-service"),
    (False, None, None, "repo"),
    (True, None, None, "repo-service"),
])
def test_image_name_fallbacks(artifacts, monkeypatch, service, configured, project, expected):
    artifacts.ms_image_name = configured
    artifacts.pl_image_name = configured
    monkeypatch.setattr(image, "get_project_name", lambda: project)
    assert image.get_image_name(is_service=service) == expected
    assert image.get_image_name("explicit", is_service=service) == "explicit"


@pytest.mark.parametrize("configured,override,expected", [(None, None, "latest"), ("v1", None, "v1"), ("v1", "v2", "v2")])
def test_image_path_tag_priority(artifacts, configured, override, expected):
    artifacts.image_tag = configured
    assert image.get_image_path("model", override) == f"region-docker.pkg.dev/project/repo/model:{expected}"
