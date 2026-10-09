from aigear.project import Project


def test_project_init_creates_scaffold_and_preserves_user_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = Project("demo", ["v1", "v2"])
    project.init()
    root = tmp_path / "demo"
    for version in ("v1", "v2"):
        for step in ("fetch_data", "preprocessing", "training", "model_service"):
            assert (root / "src/pipelines" / version / step).is_dir()
    assert (root / "cloudbuild/cloudbuild.yaml").stat().st_size > 0
    dockerfile = root / "Dockerfile.pl"
    dockerfile.write_text("user dockerfile", encoding="utf-8")
    hook = root / ".git/hooks/pre-commit"
    assert b"\r\n" not in hook.read_bytes()
    hook.write_text("user hook", encoding="utf-8")
    project.init()
    assert dockerfile.read_text(encoding="utf-8") == "user dockerfile"
    assert hook.read_text(encoding="utf-8") == "user hook"


def test_project_cli_trims_pipeline_versions(monkeypatch):
    from unittest.mock import Mock
    from aigear.cli import project_cli

    constructor = Mock()
    monkeypatch.setattr(project_cli, "Project", constructor)
    monkeypatch.setattr("sys.argv", ["aigear-init", "--name", "demo", "--pipeline_versions", "v1, v2"])
    project_cli.project_init()
    constructor.assert_called_once_with(name="demo", pipeline_versions=["v1", "v2"])
    constructor.return_value.init.assert_called_once()
