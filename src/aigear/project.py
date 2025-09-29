from pathlib import Path
import shutil
from .common.stage_logger import create_stage_logger, PipelineStage

# Use deployment stage logger for project initialization
project_logger = create_stage_logger(
    stage=PipelineStage.DEPLOYMENT,
    module_name=__name__,
    cpu_count=1,
    memory_limit="1GB",
    enable_cloud_logging=False
)


class GCPInfra:
    def __init__(self):
        pass

    def init(self):
        pass

class Project:
    def __init__(self, name: str = "template_project"):
        """
        Used to manage project related functions and information
        name: project name
        """
        self.name = name
        self._template_path = Path(__file__).resolve().parent / "template"

    def init(self):
        """
        Initialize a template according to the project name
        """
        with project_logger.stage_context() as logger:
            logger.info(f"Initializing project: {self.name}")

            project_path = Path.cwd() / self.name
            project_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created project directory: {project_path}")

            # Create directory structure
            directories = ["cloudbuild", "docs", "kms", "src", "src/pipelines"]
            for dir_name in directories:
                (project_path / dir_name).mkdir(parents=True, exist_ok=True)
            logger.info("Created project directory structure")

            # Create files
            files = [".gitignore", "docker-compose.yml", "Dockerfile",
                    "env.sample.json", "README.md", "requirements.txt"]
            for file_name in files:
                (project_path / file_name).touch(exist_ok=True)
            logger.info("Created project template files")

            logger.info("Project structure:")
            self._print_tree(project_path)

            # Copy template files
            template_files = [
                project_path / "cloudbuild" / "cloudbuild.yaml",
                project_path / "docker-compose.yml",
                project_path / "Dockerfile",
                project_path / "env.sample.json"
            ]
            for file_path in template_files:
                self._copy_file(file_path)
            logger.info("Copied template files")

            logger.info(f"Project {self.name} initialization completed")

    def _print_tree(self, path: Path, prefix=""):
        if path.is_dir():
            print(f"{prefix}{path.name}/")
            children = list(sorted(path.iterdir()))
            for i, child in enumerate(children):
                is_last = i == len(children) - 1
                self._print_tree(child, prefix + ("└── " if is_last else "├── "))
        else:
            print(f"{prefix}{path.name}")

    def _copy_file(self, file_path):
        if (not file_path.exists()) or (file_path.stat().st_size == 0):
            shutil.copy(self._template_path / file_path.name, file_path)
            print(f"Copied {file_path.name} to ({file_path})")


if __name__ == "__main__":
    Project('test').init()
