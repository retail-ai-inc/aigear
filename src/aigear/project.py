from typing import Optional, List
from pathlib import Path
import shutil


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
        (project_path / "docker-compose.yml").touch(exist_ok=True)
        (project_path / "Dockerfile").touch(exist_ok=True)
        (project_path / "env.sample.json").touch(exist_ok=True)
        (project_path / "README.md").touch(exist_ok=True)
        (project_path / "requirements.txt").touch(exist_ok=True)

        self._print_tree(project_path)

        self._copy_file(self._template_path, project_path / "cloudbuild" / "cloudbuild.yaml")
        self._copy_file(self._template_path, project_path / "docker-compose.yml")
        self._copy_file(self._template_path, project_path / "Dockerfile")
        self._copy_file(self._template_path, project_path / "env.sample.json")

        for pipeline_version in self.pipeline_versions:
            pipeline_dir = project_path / "src" / "pipelines" / pipeline_version
            pipeline_dir.mkdir(parents=True, exist_ok=True)

            fetch_data_dir = pipeline_dir / "fetch_data"
            fetch_data_dir.mkdir(parents=True, exist_ok=True)
            feature_preprocessing_dir = pipeline_dir / "preprocessing"
            feature_preprocessing_dir.mkdir(parents=True, exist_ok=True)
            training_dir = pipeline_dir / "training"
            training_dir.mkdir(parents=True, exist_ok=True)

            grpc_dir = pipeline_dir / "grpc"
            grpc_dir.mkdir(parents=True, exist_ok=True)

            (grpc_dir / "docker-compose.yml").touch(exist_ok=True)
            (grpc_dir / "Dockerfile").touch(exist_ok=True)
            (grpc_dir / "env.sample.json").touch(exist_ok=True)
            (grpc_dir / "requirements.txt").touch(exist_ok=True)

            self._print_tree(grpc_dir)

            self._copy_file(self._template_path / "grpc", grpc_dir / "docker-compose.yml")
            self._copy_file(self._template_path / "grpc", grpc_dir / "Dockerfile")
            self._copy_file(self._template_path / "grpc", grpc_dir / "docker-compose.yml")

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
