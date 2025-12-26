from pathlib import Path
import shutil
from typing import Optional, List
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class GCPInfra:
    def __init__(self):
        pass

    def init(self):
        pass

class Project:
    def __init__(
        self,
        name: str = "template_project",
        pipelines: Optional[List[str]] = None
    ):
        """
        Used to manage project related functions and information

        Args:
            name: project name
            pipelines: list of pipeline names (e.g., ["pipeline_v1", "pipeline_v2"])
        """
        self.name = name
        self.pipelines = pipelines or ["pipeline_version_1"]
        self._template_path = Path(__file__).resolve().parent / "template"

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

        # Create pipeline subdirectories with grpc_service structure
        for pipeline_name in self.pipelines:
            self._create_pipeline_structure(project_path, pipeline_name)

        (project_path / ".gitignore").touch(exist_ok=True)
        (project_path / "docker-compose.yml").touch(exist_ok=True)
        (project_path / "Dockerfile").touch(exist_ok=True)
        (project_path / "env.sample.json").touch(exist_ok=True)
        (project_path / "README.md").touch(exist_ok=True)
        (project_path / "requirements.txt").touch(exist_ok=True)

        # Copy template files first
        self._copy_file(project_path / "cloudbuild" / "cloudbuild.yaml")
        self._copy_file(project_path / "docker-compose.yml")
        self._copy_file(project_path / "Dockerfile")

        # Generate env.json with specified pipelines
        self._generate_env_config(project_path)

        self._print_tree(project_path)

        # Print quick start guide
        self._print_quick_start_guide(project_path)

    def _generate_env_config(self, project_path: Path):
        """
        Generate env.json with specified pipelines

        Args:
            project_path: Project root path
        """
        import json
        from datetime import datetime

        # Load template env.sample.json
        template_env_file = self._template_path / "env.sample.json"

        try:
            with open(template_env_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            print(f"[WARNING] 无法读取模板配置文件: {e}")
            return

        # Update basic info
        config['project_name'] = self.name
        config['last_updated'] = datetime.now().isoformat()

        # Generate pipeline configurations
        pipelines_config = {}

        for i, pipeline_name in enumerate(self.pipelines):
            port = 50051 + i

            # Copy template pipeline structure
            template_pipeline = config.get('pipelines', {}).get('pipeline_version_1', {})

            # Create new pipeline config
            pipeline_config = {
                "pipeline_parameter": {},
                "fetch_store_list": {},
                "fetch_data": {},
                "preprocessing": {},
                "training": {},
                "release": {
                    "on": True,
                    "to_bucket": True,
                    "bucket_path": f"models/releases/{pipeline_name}/"
                },
                "grpc": {
                    "server": {
                        "serviceHost": "0.0.0.0",
                        "port": str(port),
                        "modelPaths": {
                            "mode": "manifest",
                            "base_path": "/models/"
                        },
                        "multiProcessing": template_pipeline.get('grpc', {}).get('server', {}).get('multiProcessing', {
                            "on": False,
                            "processCount": 2,
                            "threadCount": 10
                        }),
                        "grpcOptions": template_pipeline.get('grpc', {}).get('server', {}).get('grpcOptions', {
                            "maxMessageSize": 52428800,
                            "keepalive": {
                                "time": 60,
                                "timeout": 5
                            }
                        })
                    },
                    "deployment": {
                        "enabled": False,
                        "gke": {
                            "enabled": False,
                            "cluster": {
                                "name": f"{pipeline_name}-cluster",
                                "location": "asia-east1",
                                "node_count": 3,
                                "machine_type": "e2-standard-4",
                                "disk_size": 100,
                                "enable_autoscaling": True,
                                "min_nodes": 1,
                                "max_nodes": 10,
                                "enable_autopilot": False
                            },
                            "deployment": {
                                "replicas": 2,
                                "resources": {
                                    "requests": {
                                        "cpu": "500m",
                                        "memory": "1Gi"
                                    },
                                    "limits": {
                                        "cpu": "2000m",
                                        "memory": "4Gi"
                                    }
                                },
                                "autoscaling": {
                                    "enabled": True,
                                    "min_replicas": 2,
                                    "max_replicas": 10,
                                    "target_cpu": 70
                                }
                            },
                            "service": {
                                "type": "LoadBalancer",
                                "port": port,
                                "annotations": {}
                            },
                            "image": {
                                "repository": f"{pipeline_name}-grpc",
                                "tag": "latest",
                                "pull_policy": "Always"
                            }
                        },
                        "docker": {
                            "enabled": True,
                            "base_image": "python:3.9-slim",
                            "working_dir": "/app",
                            "expose_ports": [port]
                        }
                    },
                    "sentry": {
                        "on": False,
                        "dsn": "https://your-sentry-dsn@sentry.io/project-id",
                        "tracesSampleRate": 1.0,
                        "environment": "production",
                        "release": "1.0.0"
                    }
                }
            }

            pipelines_config[pipeline_name] = pipeline_config

        # Update pipelines in config
        config['pipelines'] = pipelines_config

        # Write to env.sample.json (as template)
        env_sample_file = project_path / "env.sample.json"
        try:
            with open(env_sample_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

            print("\n" + "=" * 60)
            print("[OK] 已生成配置模板文件: env.sample.json")
            print("=" * 60)
            print(f"  - 项目名称: {self.name}")
            print(f"  - Pipelines: {', '.join(self.pipelines)}")
            for i, pipeline_name in enumerate(self.pipelines):
                print(f"    - {pipeline_name}: port {50051 + i}")
            print("\n  提示: 请复制 env.sample.json 为 env.json 并修改配置")
            print("  命令: cp env.sample.json env.json")
            print("=" * 60)

        except Exception as e:
            print(f"[WARNING] 写入配置文件失败: {e}")

    def _create_pipeline_structure(self, project_path: Path, pipeline_name: str):
        """
        Create directory structure for a single pipeline

        Args:
            project_path: Project root path
            pipeline_name: Pipeline name
        """
        # Create pipeline directory
        pipeline_dir = project_path / "src" / "pipelines" / pipeline_name
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        # Create grpc_service directory
        grpc_service_dir = pipeline_dir / "grpc_service"
        grpc_service_dir.mkdir(exist_ok=True)

        # Create basic files in grpc_service
        self._create_grpc_service_files(grpc_service_dir, pipeline_name)

        print(f"[OK] Created pipeline structure: {pipeline_name}")

    def _create_grpc_service_files(self, grpc_service_dir: Path, pipeline_name: str):
        """
        Create complete gRPC service structure in grpc_service directory

        Args:
            grpc_service_dir: grpc_service directory path
            pipeline_name: Pipeline name
        """
        try:
            from .generators.grpc_service_generator import GrpcServiceGenerator, ModelType
            import tempfile

            # Use a temporary directory to avoid polluting the project structure
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_output = Path(temp_dir)

                generator = GrpcServiceGenerator(
                    project_name="temp_grpc_service",
                    model_types=[ModelType.SKLEARN],
                    output_dir=temp_output,
                    silent=True  # Suppress all output
                )

                # Generate the service (silent mode)
                generator.generate()

                # Move generated files to grpc_service directory
                temp_service_path = temp_output / "temp_grpc_service"
                if temp_service_path.exists():
                    # Move all files from temp directory to grpc_service
                    # Exclude env.sample.json as it will be generated by Project.init()
                    for item in temp_service_path.iterdir():
                        # Skip env.sample.json - it should only be in project root
                        if item.name == "env.sample.json":
                            continue

                        dest = grpc_service_dir / item.name
                        if item.is_dir():
                            shutil.copytree(item, dest, dirs_exist_ok=True)
                        else:
                            shutil.copy2(item, dest)

        except Exception as e:
            import traceback
            logger.warning(f"Failed to generate full gRPC service structure: {e}")
            logger.debug(traceback.format_exc())
            logger.info("Creating minimal structure instead...")

            # Fallback: create minimal structure
            self._create_minimal_grpc_structure(grpc_service_dir)

    def _create_minimal_grpc_structure(self, grpc_service_dir: Path):
        """
        Create minimal gRPC service structure as fallback

        Args:
            grpc_service_dir: grpc_service directory path
        """
        # Create .dockerignore
        dockerignore_content = """__pycache__
*.pyc
*.pyo
*.pyd
.Python
env/
venv/
.git
.gitignore
.vscode
.idea
*.md
"""
        (grpc_service_dir / ".dockerignore").write_text(dockerignore_content, encoding='utf-8')

    def _print_tree(self, path: Path, prefix=""):
        if path.is_dir():
            print(f"{prefix}{path.name}/")
            children = list(sorted(path.iterdir()))
            for i, child in enumerate(children):
                is_last = i == len(children) - 1
                self._print_tree(child, prefix + ("└── " if is_last else "├── "))
        else:
            print(f"{prefix}{path.name}")

    def _print_quick_start_guide(self, project_path: Path):
        """
        Print quick start guide for running gRPC services

        Args:
            project_path: Project root path
        """
        import sys

        # Try to set UTF-8 encoding for Windows console
        if sys.platform == 'win32':
            try:
                import codecs
                sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
            except:
                pass  # If fails, continue with default encoding

        print("\n" + "=" * 70)
        print("[QUICK START] gRPC Service Quick Start Guide")
        print("=" * 70)

        print(f"\n[PROJECT] Project Location: {project_path.absolute()}")

        print("\n" + "-" * 70)
        print("Step 1: Navigate to Project Directory")
        print("-" * 70)
        print(f"   cd {self.name}")

        print("\n" + "-" * 70)
        print("Step 2: Configure Environment File")
        print("-" * 70)
        print("   # Copy configuration template and modify as needed")
        print("   cp env.sample.json env.json")
        print("   # Or Windows: copy env.sample.json env.json")

        print("\n" + "-" * 70)
        print("Step 3: Run gRPC Service for Each Pipeline")
        print("-" * 70)

        for i, pipeline_name in enumerate(self.pipelines):
            port = 50051 + i
            grpc_service_path = f"src/pipelines/{pipeline_name}/grpc_service"

            print(f"\n   [Pipeline] {pipeline_name}")
            print(f"   Port: {port}")
            print(f"   Path: {grpc_service_path}")
            print(f"\n   # Navigate to grpc_service directory")
            print(f"   cd {grpc_service_path}")
            print(f"\n   # Option A: Use setup script (Recommended)")
            print(f"   ./setup.sh          # Linux/Mac")
            print(f"   setup.bat           # Windows")
            print(f"\n   # Option B: Manual setup")
            print(f"   pip install -r service/requirements.txt")
            print(f"   cd service")
            print(f"   python -m grpc_tools.protoc -I../proto --python_out=proto --grpc_python_out=proto ../proto/grpc.proto")
            print(f"   # Fix import (Windows):")
            print(f'   powershell -Command "(Get-Content proto\\grpc_pb2_grpc.py) -replace \'^import grpc_pb2 as grpc__pb2\', \'from . import grpc_pb2 as grpc__pb2\' | Set-Content proto\\grpc_pb2_grpc.py"')
            print(f"   cd ..")
            print(f"\n   # Start service")
            print(f"   python service/main.py")
            print(f"\n   # Return to project root")
            print(f"   cd ../../..")

        print("\n" + "-" * 70)
        print("Step 4: Test gRPC Service")
        print("-" * 70)
        print("\n   # Run test client in another terminal window")
        for i, pipeline_name in enumerate(self.pipelines):
            grpc_service_path = f"src/pipelines/{pipeline_name}/grpc_service"
            print(f"\n   # Test {pipeline_name}")
            print(f"   cd {grpc_service_path}")
            print(f"   python test_client.py")
            print(f"   cd ../../..")

        print("\n" + "-" * 70)
        print("[IMPORTANT] Important Notes")
        print("-" * 70)
        print("\n   [OK] Configuration: Modify env.json according to your needs")
        print("   [OK] Port Allocation: Each pipeline uses a different port to avoid conflicts")

        print("\n   Port Mapping:")
        for i, pipeline_name in enumerate(self.pipelines):
            port = 50051 + i
            print(f"      - {pipeline_name}: {port}")

        print("\n   [OK] Model Files: Place trained models in models/demo/v1/ directory")
        print("   [OK] Proto Files: Edit proto/grpc.proto and recompile if interface changes needed")
        print("   [OK] Docker Deployment: Use docker-compose.yml for containerized deployment")

        print("\n" + "=" * 70)
        print("[SUCCESS] Project generated successfully! Happy coding!")
        print("=" * 70 + "\n")

    def _copy_file(self, file_path):
        if (not file_path.exists()) or (file_path.stat().st_size == 0):
            shutil.copy(self._template_path / file_path.name, file_path)
            print(f"Copied {file_path.name} to ({file_path})")


if __name__ == "__main__":
    Project('test').init()
