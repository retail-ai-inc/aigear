import shutil
from pathlib import Path
from typing import List, Optional


class Project:
    def __init__(
        self,
        name: str = "template_project",
        pipeline_versions: Optional[List[str]] = None
    ):
        """
        Used to manage project related functions and information
        name: project name
        """
        self.name = name
        self._template_path = Path(__file__).resolve().parent / "template"
        self.pipeline_versions = pipeline_versions

    def init(self):
        """
        Initialize a template according to the project name
        """

        project_path = Path.cwd() / self.name
        project_path.mkdir(parents=True, exist_ok=True)

        (project_path / "cloudbuild").mkdir(exist_ok=True)
        (project_path / "docs").mkdir(exist_ok=True)
        (project_path / "kms").mkdir(exist_ok=True)
        (project_path / "src").mkdir(exist_ok=True)
        (project_path / "src" / "pipelines").mkdir(exist_ok=True)

        (project_path / ".gitignore").touch(exist_ok=True)
        (project_path / "docker-compose-ms.yml").touch(exist_ok=True)
        (project_path / "Dockerfile.ms").touch(exist_ok=True)
        (project_path / "Dockerfile.ms.dockerignore").touch(exist_ok=True)
        (project_path / "requirements_ms.txt").touch(exist_ok=True)

        (project_path / "docker-compose-pl.yml").touch(exist_ok=True)
        (project_path / "Dockerfile.pl").touch(exist_ok=True)
        (project_path / "Dockerfile.pl.dockerignore").touch(exist_ok=True)
        (project_path / "requirements_pl.txt").touch(exist_ok=True)

        (project_path / "env.sample.json").touch(exist_ok=True)
        (project_path / "README.md").touch(exist_ok=True)

        self._copy_file(self._template_path, project_path / "cloudbuild" / "cloudbuild.yaml")

        self._copy_file(self._template_path, project_path / "docker-compose-ms.yml")
        self._copy_file(self._template_path, project_path / "Dockerfile.ms")
        self._copy_file(self._template_path, project_path / "Dockerfile.ms.dockerignore")
        self._copy_file(self._template_path, project_path / "requirements_ms.txt")

        self._copy_file(self._template_path, project_path / "docker-compose-pl.yml")
        self._copy_file(self._template_path, project_path / "Dockerfile.pl")
        self._copy_file(self._template_path, project_path / "Dockerfile.pl.dockerignore")
        self._copy_file(self._template_path, project_path / "requirements_pl.txt")

        self._copy_file(self._template_path, project_path / "env.sample.json")
        self._copy_file(self._template_path, project_path / ".gitignore")

        for pipeline_version in self.pipeline_versions:
            pipeline_dir = project_path / "src" / "pipelines" / pipeline_version
            pipeline_dir.mkdir(parents=True, exist_ok=True)

            fetch_data_dir = pipeline_dir / "fetch_data"
            fetch_data_dir.mkdir(parents=True, exist_ok=True)
            feature_preprocessing_dir = pipeline_dir / "preprocessing"
            feature_preprocessing_dir.mkdir(parents=True, exist_ok=True)
            training_dir = pipeline_dir / "training"
            training_dir.mkdir(parents=True, exist_ok=True)
            model_service = pipeline_dir / "model_service"
            model_service.mkdir(parents=True, exist_ok=True)
        
        self._print_tree(project_path)

    def _print_tree(self, path: Path, prefix=""):
        if path.is_dir():
            print(f"{prefix}{path.name}/")
            children = list(sorted(path.iterdir()))
            for i, child in enumerate(children):
                is_last = i == len(children) - 1
                self._print_tree(child, prefix + ("└── " if is_last else "├── "))
        else:
            print(f"{prefix}{path.name}")

    @staticmethod
    def _copy_file(template_path, file_path):
        if (not file_path.exists()) or (file_path.stat().st_size == 0):
            shutil.copy(template_path / file_path.name, file_path)
            print(f"Copied {file_path.name} to ({file_path})")

if __name__ == "__main__":
    Project('test').init()
