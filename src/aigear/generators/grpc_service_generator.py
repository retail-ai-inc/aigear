"""
gRPC Service Generator - Automatically generate gRPC ML service projects

This module provides the ability to automatically generate gRPC services
for machine learning model deployment.

Generated projects include:
- Standard gRPC proto definitions
- Dynamic model loading system
- Unified logging system
- Docker containerization configuration
- Environment configuration management

Usage example:
    generator = GrpcServiceGenerator(
        project_name="my_service",
        model_types=[ModelType.SKLEARN, ModelType.CATBOOST]
    )
    generator.generate()
"""

from pathlib import Path
from typing import List, Dict, Optional
from enum import Enum
from datetime import datetime
import shutil
import json
from jinja2 import Environment, FileSystemLoader, Template
import logging

# TODO will use state logger 
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
generator_logger = logging.getLogger(__name__)


class ModelType(Enum):
    """Supported model types"""
    SKLEARN = "sklearn"          # scikit-learn + joblib
    PYTORCH = "pytorch"          # PyTorch
    CATBOOST = "catboost"        # CatBoost
    RANKFM = "rankfm"           # RankFM recommendation
    RECBOLE = "recbole"         # RecBole recommendation framework
    CUSTOM = "custom"           # Custom model


class ServiceTemplate(Enum):
    """Service template types"""
    SIMPLE = "simple"  # Single model service
    MULTI_VERSION = "multi_version"  # Deprecated, kept for compatibility
    MULTI_COMPANY = "multi_company"  # Deprecated, kept for compatibility


class GrpcServiceGenerator:
    """gRPC Service Generator"""

    # Model type dependencies mapping
    MODEL_DEPENDENCIES = {
        ModelType.SKLEARN: [
            "scikit-learn>=1.0.2",
            "joblib>=1.4.2"
        ],
        ModelType.PYTORCH: [
            "torch>=2.0.0",
            "torchvision>=0.15.0"
        ],
        ModelType.CATBOOST: [
            "catboost>=1.2.0"
        ],
        ModelType.RANKFM: [
            "rankfm>=0.2.5"
        ],
        ModelType.RECBOLE: [
            "recbole>=1.2.0",
            "torch>=2.0.0"
        ]
    }

    # Base dependencies (required for all projects)
    BASE_DEPENDENCIES = [
        "grpcio>=1.54.2",
        "protobuf>=4.23.3",
        "grpcio-health-checking>=1.56.0",
        "sentry-sdk>=1.29.2",
        "pandas>=1.5.3",
        "numpy>=1.24.0"
    ]

    def __init__(
        self,
        project_name: str,
        model_types: Optional[List[ModelType]] = None,
        output_dir: Optional[Path] = None,
        features: Optional[Dict] = None,
        model_files: Optional[Dict] = None,
        silent: bool = False
    ):
        """
        Initialize gRPC Service Generator (Simple mode only)

        Args:
            project_name: Project name
            model_types: Model type list (default: [SKLEARN])
            output_dir: Output directory (default: current directory)
            features: Feature configuration (sentry, health_check, keepalive, etc.)
            model_files: Model file configuration (for generating modelPaths)
            silent: Suppress all output messages (default: False)
        """
        self.project_name = project_name
        self.model_types = model_types or [ModelType.SKLEARN]
        self.output_dir = output_dir or Path.cwd()
        self.silent = silent

        # Simple mode: fixed company and version
        self.companies = ["demo"]
        self.versions = ["v1"]
        self.template_type = ServiceTemplate.SIMPLE

        # Feature configuration (new)
        self.features = features or {
            'sentry': True,
            'health_check': True,
            'keepalive': True,
            'multi_processing': True,
            'max_message_size': 52428800,  # 50MB
        }

        # Model file configuration (new)
        self.model_files = model_files or {}

        # Template directory
        self._template_dir = Path(__file__).resolve().parent.parent / "template" / "grpc"
        self._jinja_env = Environment(
            loader=FileSystemLoader(str(self._template_dir)),
            trim_blocks=True,
            lstrip_blocks=True
        )

        # Project root directory
        self.project_path = self.output_dir / project_name

    def generate(self):
        """Generate complete gRPC service project"""
        if not self.silent:
            generator_logger.info(f"Starting gRPC service project generation: {self.project_name}")
            generator_logger.info(f"Template type: {self.template_type.value}")
            generator_logger.info(f"Model types: {[m.value for m in self.model_types]}")

        # 1. Create directory structure
        self._create_directory_structure()
        if not self.silent:
            generator_logger.info("✓ Directory structure created")

        # 2. Generate proto files
        self._generate_proto()
        if not self.silent:
            generator_logger.info("✓ Proto files generated")

        # 3. Generate main.py
        self._generate_main()
        if not self.silent:
            generator_logger.info("✓ main.py generated")

        # 4. Generate grpc_package
        self._generate_grpc_package()
        if not self.silent:
            generator_logger.info("✓ grpc_package generated")

        # 4.5. Generate config parser (new)
        self._generate_config_parser()
        if not self.silent:
            generator_logger.info("✓ Config parser generated")

        # 4.6. Generate base classes (new)
        self._generate_base_classes()
        if not self.silent:
            generator_logger.info("✓ Base classes generated")

        # 5. Generate model modules and examples
        self._generate_model_modules()
        if not self.silent:
            generator_logger.info("✓ Model modules generated")

        # 6. Generate configuration files
        self._generate_config()
        if not self.silent:
            generator_logger.info("✓ Configuration files generated")

        # 7. Generate Docker files
        self._generate_docker_files()
        if not self.silent:
            generator_logger.info("✓ Docker files generated")

        # 8. Generate requirements.txt
        self._generate_requirements()
        if not self.silent:
            generator_logger.info("✓ requirements.txt generated")

        # 9. Generate README
        self._generate_readme()
        if not self.silent:
            generator_logger.info("✓ README.md generated")

        # 10. Generate test client (new)
        self._generate_test_client()
        if not self.silent:
            generator_logger.info("✓ Test client generated")

        # 11. Generate setup scripts (new)
        self._generate_setup_scripts()
        if not self.silent:
            generator_logger.info("✓ Setup scripts generated")

        # 11.5. Generate dummy models script (new)
        self._generate_dummy_models_script()
        if not self.silent:
            generator_logger.info("✓ Dummy models script generated")

        # 12. Generate .gitignore (new)
        self._generate_gitignore()
        if not self.silent:
            generator_logger.info("✓ .gitignore generated")

        # 13. Create models directory structure (new)
        self._create_models_directory()
        if not self.silent:
            generator_logger.info("✓ Models directory created")

        # 13.5. Generate example models with manifest (new)
        self._generate_example_models_with_manifest()
        if not self.silent:
            generator_logger.info("✓ Example models and manifest generated")

        # 14. Print project structure (skip if silent)
        if not self.silent:
            generator_logger.info("\nProject structure:")
            self._print_tree(self.project_path)

            generator_logger.info(f"\n✨ Project generation completed!")
            generator_logger.info(f"📁 Project path: {self.project_path}")
            generator_logger.info(f"\n📝 Quick start:")
            generator_logger.info(f"   cd {self.project_name}")
            generator_logger.info(f"   ./setup.sh  (or setup.bat on Windows)")
            generator_logger.info(f"\n📝 Or manual setup:")
            generator_logger.info(f"   1. cd {self.project_name}")
            generator_logger.info(f"   2. cp env.sample.json env.json")
            generator_logger.info(f"   3. pip install -r service/requirements.txt")
            generator_logger.info(f"   4. cd service && python -m grpc_tools.protoc -I../proto --python_out=proto --grpc_python_out=proto ../proto/grpc.proto")
            generator_logger.info(f"   5. python main.py")
            generator_logger.info(f"   6. (In another terminal) python ../test_client.py")

    def _create_directory_structure(self):
        """Create project directory structure"""
        # Main directory
        self.project_path.mkdir(parents=True, exist_ok=True)

        # Service directory
        service_dir = self.project_path / "service"
        service_dir.mkdir(exist_ok=True)

        # grpc_package directory
        (service_dir / "grpc_package").mkdir(exist_ok=True)
        (service_dir / "grpc_package" / "__init__.py").touch()

        # proto directory
        (service_dir / "proto").mkdir(exist_ok=True)
        (service_dir / "proto" / "__init__.py").touch()

        # models_modules directory
        models_dir = service_dir / "models_modules"
        models_dir.mkdir(exist_ok=True)
        (models_dir / "__init__.py").touch()

        # Simple template: only one model directory
        model_dir = models_dir / "model"
        model_dir.mkdir(exist_ok=True)
        (model_dir / "__init__.py").touch()

        # Other directories
        (self.project_path / "proto").mkdir(exist_ok=True)
        (self.project_path / "cloudbuild").mkdir(exist_ok=True)
        (self.project_path / "docs").mkdir(exist_ok=True)
        (self.project_path / "kms").mkdir(exist_ok=True)

    def _generate_proto(self):
        """Generate proto files"""
        proto_content = '''syntax = "proto3";
import "google/protobuf/struct.proto";
option go_package = "./proto";

service ML {
  rpc Predict(MLRequest) returns (MLResponse) {}
}

message MLRequest {
  google.protobuf.Struct request = 1;
}

message MLResponse {
  google.protobuf.Struct response = 2;
}
'''
        proto_file = self.project_path / "proto" / "grpc.proto"
        proto_file.write_text(proto_content)

    def _generate_main(self):
        """Generate main.py"""
        # Simple mode: no multi-company or multi-version support
        multi_company = False
        multi_version = False

        main_template = self._get_main_template()

        main_content = main_template.render(
            multi_company=multi_company,
            multi_version=multi_version,
            template_type=self.template_type.value
        )

        main_file = self.project_path / "service" / "main.py"
        main_file.write_text(main_content)

    def _generate_grpc_package(self):
        """Generate files in grpc_package directory"""
        grpc_package_dir = self.project_path / "service" / "grpc_package"

        # 1. grpc_ml_module.py - Model loader
        ml_module_template = self._get_ml_module_template()
        ml_module_content = ml_module_template.render(
            multi_company=False,
            multi_version=False
        )
        (grpc_package_dir / "grpc_ml_module.py").write_text(ml_module_content)

        # 2. grpc_log.py - Logging system (fixed content)
        log_content = self._get_log_module_content()
        (grpc_package_dir / "grpc_log.py").write_text(log_content)

        # 3. grpc_features.py - Utility functions
        features_content = self._get_features_module_content()
        (grpc_package_dir / "grpc_features.py").write_text(features_content)

    def _generate_model_modules(self):
        """Generate model modules"""
        models_dir = self.project_path / "service" / "models_modules"

        # Simple mode: only one model directory
        self._generate_simple_model(models_dir / "model")

    def _generate_simple_model(self, model_dir: Path):
        """Generate simple model example"""
        # Generate example based on first model type
        primary_model = self.model_types[0]

        model_template = self._get_model_template(primary_model)
        model_content = model_template.render(
            model_type=primary_model.value
        )

        # Generate Model.py with UTF-8 encoding
        (model_dir / "Model.py").write_text(model_content, encoding='utf-8')

    def _generate_config(self):
        """Generate or update unified configuration file (NEW: unified config management)"""
        # Build gRPC configuration
        grpc_config = self._build_config_structure()

        # Find project root directory by searching upward for existing config
        project_root = self._find_project_root()

        # Check if env.json or env.sample.json exists in project root
        env_json = project_root / "env.json"
        env_sample_json = project_root / "env.sample.json"

        if env_json.exists():
            # Update existing env.json
            self._update_config_file(env_json, grpc_config)
            if not self.silent:
                generator_logger.info(f"✓ Updated existing configuration: {env_json}")
        elif env_sample_json.exists():
            # Update existing env.sample.json
            self._update_config_file(env_sample_json, grpc_config)
            if not self.silent:
                generator_logger.info(f"✓ Updated existing configuration: {env_sample_json}")
        else:
            # Create new env.sample.json in project root
            env_sample_json.write_text(json.dumps(grpc_config, indent=2, ensure_ascii=False))
            if not self.silent:
                generator_logger.info(f"✓ Created new configuration file: {env_sample_json}")

    def _find_project_root(self) -> Path:
        """
        Find project root directory by searching upward

        Search strategy:
        1. Check if output_dir has env.json or env.sample.json -> use it
        2. Search parent directories (up to 3 levels) for config files
        3. Search for .git directory
        4. Fallback to output_dir

        Returns:
            Path to project root directory
        """
        current_dir = self.output_dir

        # Strategy 1: Check current output_dir
        if (current_dir / "env.json").exists() or (current_dir / "env.sample.json").exists():
            return current_dir

        # Strategy 2: Search parent directories for config files
        # Stop if we encounter a .git directory (project boundary)
        for _ in range(3):
            parent = current_dir.parent
            if parent == current_dir:  # Reached filesystem root
                break

            # Stop if parent has .git directory (different project)
            if (parent / ".git").exists():
                if not self.silent:
                    generator_logger.info(f"Stopped at project boundary (.git found in {parent})")
                break

            if (parent / "env.json").exists() or (parent / "env.sample.json").exists():
                if not self.silent:
                    generator_logger.info(f"Found project root with config: {parent}")
                return parent

            current_dir = parent

        # Strategy 3: Check if output_dir itself has .git directory
        # (Don't search upward to avoid crossing project boundaries)
        if (self.output_dir / ".git").exists():
            if not self.silent:
                generator_logger.info(f"Found project root with .git: {self.output_dir}")
            return self.output_dir

        # Fallback: use output_dir
        if not self.silent:
            generator_logger.info(f"Using output_dir as project root: {self.output_dir}")
        return self.output_dir

    def _update_config_file(self, config_file: Path, grpc_config: Dict):
        """
        Update existing configuration file with gRPC config

        Args:
            config_file: Path to existing configuration file
            grpc_config: New gRPC configuration to merge
        """
        # Load existing config
        with open(config_file, 'r', encoding='utf-8') as f:
            existing_config = json.load(f)

        # Ensure grpc section exists
        if 'grpc' not in existing_config:
            existing_config['grpc'] = {}

        # Merge gRPC configuration
        # For multi-company mode: update grpc.servers
        if 'servers' in grpc_config.get('grpc', {}):
            if 'servers' not in existing_config['grpc']:
                existing_config['grpc']['servers'] = {}
            # Merge server configurations
            existing_config['grpc']['servers'].update(grpc_config['grpc']['servers'])

        # For simple mode: update grpc.server
        if 'server' in grpc_config.get('grpc', {}):
            existing_config['grpc']['server'] = grpc_config['grpc']['server']

        # Update other grpc-level configurations (sentry, etc.)
        for key in ['sentry', 'logging']:
            if key in grpc_config.get('grpc', {}):
                existing_config['grpc'][key] = grpc_config['grpc'][key]

        # Write back
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(existing_config, f, indent=2, ensure_ascii=False)

    def _build_config_structure(self) -> Dict:
        """Build configuration structure (v1.0.0 standard - pipeline independent grpc)"""
        config = {
            "config_version": "1.0.0",
            "config_schema": "aigear-pipeline",
            "last_updated": datetime.now().isoformat(),
            "project_name": self.project_name,
            "environment": "local",
            "pipelines": {}
        }

        # Build pipeline configurations
        # Use companies as pipeline names
        for i, pipeline_name in enumerate(self.companies):
            port = 50051 + i

            # Build model paths
            model_paths = self._build_model_paths(pipeline_name, "v1")

            config["pipelines"][pipeline_name] = {
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
                        "modelPaths": model_paths,
                        "multiProcessing": {
                            "on": self.features.get('multi_processing', False),
                            "processCount": 2,
                            "threadCount": 10
                        },
                        "grpcOptions": {
                            "maxMessageSize": self.features.get('max_message_size', 52428800),
                            "keepalive": {
                                "time": 60,
                                "timeout": 5
                            } if self.features.get('keepalive', True) else {}
                        }
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
                    } if self.features.get('sentry', True) else {"on": False}
                }
            }

        return config

    def _get_preset_name(self) -> str:
        """
        Get preset name based on model types

        Returns:
            str: Preset name (e.g., 'recommendation', 'custom')
        """
        # Check if using recommendation models
        if ModelType.RANKFM in self.model_types or ModelType.RECBOLE in self.model_types:
            return "recommendation"

        # Default to custom
        return "custom"

    def _build_model_paths(self, company: str, version: str) -> Dict:
        """
        Build modelPaths configuration for a single version

        Args:
            company: Company code
            version: Version number

        Returns:
            Model file path dictionary
        """
        # Check if custom model_files configuration is available
        if version in self.model_files:
            config = self.model_files[version]

            # Mode 1: If config is a dict with full paths, use it directly
            if isinstance(config, dict) and any(path.startswith('gs://') or path.startswith('/') for path in config.values()):
                # Replace template variables if enabled
                if self.features.get('template_variables', False):
                    model_paths = {}
                    for key, path in config.items():
                        # Replace ${company} and ${version} placeholders
                        path = path.replace('${company}', company)
                        path = path.replace('${version}', version)
                        model_paths[key] = path
                    return model_paths
                else:
                    return config

            # Mode 2: If config is a list of file names, generate paths
            elif isinstance(config, list):
                file_list = config
            else:
                # Fallback: treat as list
                file_list = ['model_file']

        elif 'default' in self.model_files:
            config = self.model_files['default']

            # Same logic for default configuration
            if isinstance(config, dict) and any(path.startswith('gs://') or path.startswith('/') for path in config.values()):
                if self.features.get('template_variables', False):
                    model_paths = {}
                    for key, path in config.items():
                        path = path.replace('${company}', company)
                        path = path.replace('${version}', version)
                        model_paths[key] = path
                    return model_paths
                else:
                    return config
            elif isinstance(config, list):
                file_list = config
            else:
                file_list = ['model_file']
        else:
            # Default configuration: Use Manifest mode (NEW)
            # Check if manifest mode is enabled (default: True)
            use_manifest = self.features.get('use_manifest', True)

            if use_manifest:
                # Return Manifest mode configuration
                base_path = f"/models/{company}/{version}/"
                if self.features.get('template_variables', False):
                    base_path = f"/models/${{company}}/${{version}}/"

                return {
                    "mode": "manifest",
                    "base_path": base_path
                }
            else:
                # Fallback to explicit path mode (for backward compatibility)
                file_list = ['model_file']

        # Generate path dictionary from file list (only for explicit path mode)
        model_paths = {}
        for file_key in file_list:
            # Support template variables
            if self.features.get('template_variables', False):
                path = f"/models/${{company}}/${{version}}/{file_key}.pkl"
            else:
                path = f"/models/{company}/{version}/{file_key}.pkl"

            model_paths[file_key] = path

        return model_paths

    def _generate_docker_files(self):
        """Generate Docker related files"""
        # 1. Dockerfile
        dockerfile_content = self._get_dockerfile_content()
        (self.project_path / "Dockerfile-grpc").write_text(dockerfile_content)

        # 2. docker-compose.yml
        compose_content = self._get_docker_compose_content()
        (self.project_path / "docker-compose.yml").write_text(compose_content)

        # 3. .dockerignore
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
        (self.project_path / ".dockerignore").write_text(dockerignore_content)

    def _generate_requirements(self):
        """
        Generate requirements.txt (auto-merge)

        New standard:
        - Generate requirements.txt (merged all dependencies)
        - Generate requirements_base.txt (base dependencies)
        - Generate requirements_{version}.txt by version (optional)
        """
        # Base dependencies
        base_requirements = self.BASE_DEPENDENCIES.copy()

        # Model-specific dependencies
        model_requirements = []
        for model_type in self.model_types:
            model_requirements.extend(self.MODEL_DEPENDENCIES.get(model_type, []))

        # Merge and deduplicate
        all_requirements = sorted(set(base_requirements + model_requirements))

        service_dir = self.project_path / "service"

        # 1. Generate merged requirements.txt
        req_file = service_dir / "requirements.txt"
        req_file.write_text("\n".join(all_requirements) + "\n")

        # 2. Generate requirements_base.txt (base dependencies only)
        base_req_file = service_dir / "requirements_base.txt"
        base_req_file.write_text("\n".join(sorted(set(base_requirements))) + "\n")

        # 3. Generate version-specific requirements files (if model_files configured)
        if self.model_files:
            for version, file_list in self.model_files.items():
                if version == 'default':
                    continue

                # Generate specific dependencies for each version
                version_requirements = base_requirements.copy()

                # Infer model type based on version (simplified logic, can be extended later)
                version_req_file = service_dir / f"requirements_{version}.txt"
                version_requirements.extend(model_requirements)

                version_req_file.write_text("\n".join(sorted(set(version_requirements))) + "\n")

    def _generate_readme(self):
        """Generate README.md"""
        readme_content = f"""# {self.project_name}

This is a gRPC machine learning service project generated using aigear.

## Project Information

- **Template Type**: {self.template_type.value}
- **Model Types**: {', '.join([m.value for m in self.model_types])}
- **Companies**: {', '.join(self.companies)}
- **Versions**: {', '.join(self.versions)}

## Quick Start

### 1. Install Dependencies

```bash
cd service
pip install -r requirements.txt
```

### 2. Compile Proto Files

```bash
python -m grpc_tools.protoc -I../proto --python_out=proto --grpc_python_out=proto ../proto/grpc.proto
```

### 3. Implement Models

Edit model files under `service/models_modules/` and implement the `predict()` method.

### 4. Configure Environment

Copy `env.sample.json` to `env.json` and modify the configuration:

```bash
cp env.sample.json env.json
```

### 5. Run Service

#### Local Run

```bash
cd service
python main.py
```

#### Docker Run

```bash
docker-compose up
```

## Project Structure

```
{self.project_name}/
├── service/               # gRPC service code
│   ├── main.py            # Main entry point
│   ├── grpc_package/      # gRPC toolkit
│   ├── models_modules/    # Model implementations
│   ├── proto/             # Compiled proto files
│   └── requirements.txt   # Python dependencies
├── proto/                 # Proto definitions
├── docker-compose.yml     # Docker orchestration
├── Dockerfile-grpc        # Docker image
└── env.sample.json        # Configuration example
```

## API Usage

### gRPC Interface

```python
import grpc
from proto import grpc_pb2, grpc_pb2_grpc
from google.protobuf import struct_pb2

# Create connection
channel = grpc.insecure_channel('localhost:50051')
stub = grpc_pb2_grpc.MLStub(channel)

# Build request
request_data = struct_pb2.Struct()
request_data.update({{"your": "data"}})
request = grpc_pb2.MLRequest(request=request_data)

# Call prediction
response = stub.Predict(request)
print(response.response)
```

## Development Guide

### Add New Model

1. Create new model file under `models_modules/`
2. Inherit base class and implement `predict()` method
3. Update `env.json` configuration

### Add New Company/Version

1. Create corresponding directory under `models_modules/`
2. Implement model file
3. Update `env.json` and `docker-compose.yml`

## License

MIT
"""
        readme_file = self.project_path / "README.md"
        readme_file.write_text(readme_content)

    def _print_tree(self, path: Path, prefix="", max_depth=3, current_depth=0):
        """Print directory tree"""
        if current_depth >= max_depth:
            return

        if path.is_dir():
            print(f"{prefix}{path.name}/")
            children = sorted([p for p in path.iterdir() if not p.name.startswith('.')])
            for i, child in enumerate(children):
                is_last = i == len(children) - 1
                extension = "    " if is_last else "│   "
                self._print_tree(
                    child,
                    prefix + extension,
                    max_depth,
                    current_depth + 1
                )
        else:
            print(f"{prefix}{path.name}")

    # ========== Template Content Generation Methods ==========

    def _get_main_template(self) -> Template:
        """Get main.py template (new standard: supports modelPaths + KeepAlive + ConfigParser)"""
        template_str = '''"""
gRPC ML Service - Machine Learning Model Service

Auto-generated gRPC service with dynamic model loading and prediction support.

New standard features:
- modelPaths configuration (supports multiple model files)
- KeepAlive configuration
- Health Check
- Sentry integration
- ConfigParser configuration parser
"""

import grpc
from concurrent import futures
import multiprocessing
import argparse
import os
import signal
import time
import sys
from pathlib import Path

from google.protobuf import struct_pb2
from google.protobuf.json_format import MessageToDict
from grpc_health.v1 import health
from grpc_health.v1 import health_pb2_grpc
from sentry_sdk import init as sentry_init
from sentry_sdk.integrations.grpc.server import ServerInterceptor

# Import gRPC modules
from proto import grpc_pb2, grpc_pb2_grpc

# Import custom modules
from grpc_package import grpc_ml_module, grpc_log, grpc_features, grpc_config

# Initialize logger
logger = grpc_log.GrpcLog().grpc_log()


class MLServicer(grpc_pb2_grpc.MLServicer):
    """ML Service Implementation"""

    def __init__(self, model_instance):
        self.model_service = model_instance

    def Predict(self, request, context):
        """Predict method"""
        try:
            # Parse request
            request_dict = MessageToDict(request).get('request', {})
            logger.info(f"Received prediction request: {request_dict}")

            # Call model prediction
            model_out = self.model_service.predict(request_dict)
            logger.info(f"Prediction result: {model_out}")

            # Build response
            response_data = struct_pb2.Struct()
            response_data.update({"response": model_out})

            return grpc_pb2.MLResponse(response=response_data)

        except Exception as e:
            logger.error(f"Prediction error: {str(e)}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Prediction failed: {str(e)}")
            return grpc_pb2.MLResponse()


def _run_server(bind_address: str, model_instance, grpc_options: dict, use_sentry: bool):
    """Run gRPC server"""
    logger.info(f"Starting server on {bind_address}")

    # Build gRPC options
    max_message_size = grpc_options.get('maxMessageSize', 52428800)  # 50MB
    options = [
        ("grpc.so_reuseport", 1),
        ('grpc.max_send_message_length', max_message_size),
        ('grpc.max_receive_message_length', max_message_size),
    ]

    # Add KeepAlive configuration
    keepalive_config = grpc_options.get('keepalive', {})
    if keepalive_config:
        keepalive_time = keepalive_config.get('time', 60)
        keepalive_timeout = keepalive_config.get('timeout', 5)
        options.extend([
            ('grpc.keepalive_time_ms', keepalive_time * 1000),
            ('grpc.keepalive_timeout_ms', keepalive_timeout * 1000),
            ('grpc.keepalive_permit_without_calls', True),
        ])
        logger.info(f"KeepAlive enabled: time={keepalive_time}s, timeout={keepalive_timeout}s")

    # Create server with optional Sentry interceptor
    interceptors = [ServerInterceptor()] if use_sentry else []
    server = grpc.server(
        thread_pool=futures.ThreadPoolExecutor(max_workers=grpc_options.get('threadCount', 10)),
        interceptors=interceptors,
        options=options
    )

    grpc_pb2_grpc.add_MLServicer_to_server(MLServicer(model_instance), server)

    # Add Health Check service
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    logger.info("Health Check service enabled")

    server.add_insecure_port(bind_address)
    server.start()

    # Wait for termination
    def sigterm_handler(signum, frame):
        logger.info("Received SIGTERM, shutting down...")
        server.stop(grace=5)

    signal.signal(signal.SIGTERM, sigterm_handler)

    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.stop(0)


def main():
    """Main function"""
    logger.info("=" * 60)
    logger.info("Starting gRPC ML Service")
    logger.info("=" * 60)

    # Load configuration using ConfigParser
    config_parser = grpc_config.ConfigParser("env.json")

    # Simple mode: get first pipeline configuration
    pipelines = config_parser.config.get('pipelines', {})
    if not pipelines:
        logger.error("No pipeline configuration found in env.json!")
        sys.exit(1)

    # Get first pipeline name
    pipeline_name = list(pipelines.keys())[0]
    pipeline_config = pipelines[pipeline_name]

    # Get server configuration
    server_config = pipeline_config.get('grpc', {}).get('server', {})
    service_host = server_config.get('serviceHost', '0.0.0.0')
    port = server_config.get('port', '50051')

    # Get model paths (modelPaths format)
    model_paths = server_config.get('modelPaths', {})

    logger.info(f"Model paths configuration: {model_paths}")

    # Get gRPC options
    grpc_options = server_config.get('grpcOptions', {})
    grpc_options['threadCount'] = server_config.get('multiProcessing', {}).get('threadCount', 10)

    # Get multiprocessing configuration
    multi_processing_config = server_config.get('multiProcessing', {})
    is_multi_processing = multi_processing_config.get('on', False)
    process_count = multi_processing_config.get('processCount', 1)

    # Get Sentry configuration
    sentry_config = pipeline_config.get('grpc', {}).get('sentry', {})
    sentry_enabled = sentry_config.get('on', False)

    if sentry_enabled:
        sentry_init(
            dsn=sentry_config.get('dsn'),
            traces_sample_rate=sentry_config.get('tracesSampleRate', 1.0),
            environment=config_parser.config.get('environment', 'local'),
        )
        logger.info("Sentry enabled")

    # Simple mode: directly load the model class
    # No dynamic loading based on company/version
    from models_modules.model.Model import Model
    model_class = Model

    if model_class is None:
        logger.error("Model class not found!")
        sys.exit(1)

    logger.info(f"Model class loaded: {model_class.__name__}")

    # Initialize model with modelPaths (new standard)
    model_instance = model_class(model_paths)

    # Log model file information
    for model_name, model_path in model_paths.items():
        if os.path.exists(model_path):
            file_size_bytes = os.path.getsize(model_path)
            file_size_mb = file_size_bytes / (1024 * 1024)
            modification_time = time.ctime(os.path.getmtime(model_path))

            logger.info(f"Loaded model '{model_name}':")
            logger.info(f"  - Path: {model_path}")
            logger.info(f"  - Size: {file_size_mb:.2f} MB")
            logger.info(f"  - Last Modified: {modification_time}")
        else:
            logger.warning(f"Model file not found: {model_path}")

    # Start server
    with grpc_features.reserve_port(int(port)) as grpc_port:
        bind_address = f"{service_host}:{grpc_port}"

        if is_multi_processing:
            # Multi-process mode
            logger.info(f"Starting {process_count} worker processes")
            sys.stdout.flush()
            workers = []
            for i in range(process_count):
                worker = multiprocessing.Process(
                    target=_run_server,
                    args=(bind_address, model_instance, grpc_options, sentry_enabled)
                )
                worker.start()
                workers.append(worker)
                logger.info(f"Worker {i+1} started (PID: {worker.pid})")

            # Wait for all workers
            for worker in workers:
                worker.join()
        else:
            # Single process mode
            _run_server(bind_address, model_instance, grpc_options, sentry_enabled)


if __name__ == '__main__':
    logger = grpc_log.GrpcLog().grpc_log()

    # Simple mode: no command line arguments needed
    main()
'''
        return Template(template_str)

    def _get_ml_module_template(self) -> Template:
        """Get model loader template"""
        template_str = '''"""
Dynamic Model Loader

Dynamically loads the corresponding model class based on company code and version.
"""

import glob
import importlib
from pathlib import Path


class MLModule:
    """Dynamic model loader"""

    def __init__(self, company_code: str{% if multi_version %}, version: str{% endif %}):
        self.company_code = company_code
        {% if multi_version %}
        self.version = version
        {% endif %}

    def load_module(self):
        """Dynamically load model module"""
        module_path = self.find_module_path()

        if not module_path:
            raise ImportError(f"Cannot find model module for company={self.company_code}{% if multi_version %}, version={self.version}{% endif %}")

        # Import module
        module = importlib.import_module(module_path)

        # Get model class (assumed to be named 'Model')
        model_class = getattr(module, 'Model', None)

        if model_class is None:
            raise AttributeError(f"Module {module_path} does not have 'Model' class")

        return model_class

    def find_module_path(self):
        """Find model module path"""
        {% if multi_company and multi_version %}
        # Multi-company multi-version: models_modules/{company}/{version}/*.py
        pattern = f'models_modules/{self.company_code}/{self.version}/*.py'
        {% elif multi_version %}
        # Multi-version: models_modules/{version}/*.py
        pattern = f'models_modules/{self.version}/*.py'
        {% else %}
        # Simple mode: models_modules/model/*.py
        pattern = 'models_modules/model/*.py'
        {% endif %}

        files = glob.glob(pattern)

        if not files:
            return None

        # Convert to module path
        module_path = files[0].replace("\\\\", ".").replace("/", ".").replace(".py", "")

        return module_path
'''
        return Template(template_str)

    def _get_log_module_content(self) -> str:
        """Get log module content"""
        return '''"""
gRPC Logger - JSON Format Logging System
"""

import logging
import json
import sys


class JsonFormatter(logging.Formatter):
    """JSON formatter"""

    def format(self, record):
        log_data = {
            'process': record.process,
            'timestamp': self.formatTime(record),
            'level': record.levelname,
            'message': record.getMessage(),
        }

        # Add exception information
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)

        return json.dumps(log_data, ensure_ascii=False)


class GrpcLog:
    """gRPC logger"""

    def grpc_log(self):
        """Create logger"""
        logger = logging.getLogger(__name__)

        # Avoid duplicate handlers
        if logger.handlers:
            return logger

        # Create handler
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())

        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        return logger
'''

    def _get_features_module_content(self) -> str:
        """Get utility functions module content"""
        return '''"""
gRPC Features - Utility Functions
"""

import signal
import time
import socket
import argparse
from contextlib import contextmanager


def wait_until_closed(server):
    """Wait until server is closed"""
    def sigterm_handler(signum, frame):
        server.stop(grace=None)

    signal.signal(signal.SIGTERM, sigterm_handler)

    try:
        while True:
            time.sleep(60 * 60)
    except KeyboardInterrupt:
        sigterm_handler(signal.SIGTERM, None)


@contextmanager
def reserve_port(port):
    """Reserve port (for multi-process sharing)"""
    sock = socket.socket(
        socket.AF_INET6 if socket.has_ipv6 else socket.AF_INET,
        socket.SOCK_STREAM
    )
    # Use SO_REUSEADDR on Windows, SO_REUSEPORT on Unix
    if hasattr(socket, 'SO_REUSEPORT'):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", port))
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


def get_argument():
    """Get command line arguments"""
    parser = argparse.ArgumentParser(description='gRPC ML Service')
    parser.add_argument('--company', default="", help='Company code')
    parser.add_argument('--version', default="", help='Model version')
    args = parser.parse_args()
    return args.company, args.version
'''

    def _get_model_template(self, model_type: ModelType) -> Template:
        """Get model template"""
        if model_type == ModelType.SKLEARN:
            template_str = '''"""
Model Implementation - Scikit-learn Model

This is a working example that uses the auto-generated example models.
The example includes a LogisticRegression model and a StandardScaler.
"""

import joblib
import os
import numpy as np
from pathlib import Path


class DemoModel:
    """Simple demo model that returns random predictions (for testing)"""

    def predict(self, X):
        """Return binary predictions (0 or 1)"""
        np.random.seed(42)
        n_samples = len(X) if hasattr(X, '__len__') else 1
        return np.random.randint(0, 2, size=n_samples)

    def predict_proba(self, X):
        """Return prediction probabilities"""
        np.random.seed(42)
        n_samples = len(X) if hasattr(X, '__len__') else 1
        probs = np.random.dirichlet(np.ones(2), size=n_samples)
        return probs


class Model:
    """Scikit-learn model wrapper with multi-model support"""

    def __init__(self, model_paths):
        """
        Initialize model with multiple model files

        Args:
            model_paths: Model file paths dictionary, for example:
                         {
                             "example_model": "/path/to/example_model.pkl",
                             "scaler": "/path/to/scaler.pkl"
                         }
        """
        self.model_paths = model_paths if isinstance(model_paths, dict) else {"model": model_paths}
        self.demo_mode = False
        self.models = self.load_models()

    def load_models(self):
        """Load all models from paths dictionary"""
        models = {}

        print(f"[INFO] Loading {len(self.model_paths)} model files...")

        for model_name, model_path in self.model_paths.items():
            if os.path.exists(model_path):
                try:
                    model = joblib.load(model_path)
                    models[model_name] = model
                    print(f"[OK] Loaded '{model_name}' from {model_path}")
                except Exception as e:
                    print(f"[WARNING] Error loading '{model_name}': {e}")
            else:
                print(f"[WARNING] Model file not found: {model_path}")

        # Check if we have the required models
        if not models:
            print("[WARNING] No models loaded - using demo mode")
            self.demo_mode = True
            models['demo'] = self._create_demo_model()

        return models

    def _create_demo_model(self):
        """Create a simple demo model for testing"""
        return DemoModel()

    def predict(self, data: dict):
        """
        Prediction method with preprocessing support

        Args:
            data: Input data dictionary, for example:
                  {
                      "features": [[1.0, 2.0, 3.0, 4.0, 5.0]]
                  }

        Returns:
            Prediction result with predictions and probabilities
        """
        if self.demo_mode:
            print("[DEMO] Running in DEMO mode - using simulated predictions")

        # Extract features from input
        if 'features' in data:
            features = np.array(data['features'])
        else:
            # Convert dict to feature array
            feature_values = [v for k, v in sorted(data.items()) if k.startswith('feature')]
            if not feature_values:
                feature_values = [v for v in data.values() if isinstance(v, (int, float))]
            features = np.array([feature_values])

        # Apply preprocessing if scaler is available
        if 'scaler' in self.models and not self.demo_mode:
            try:
                features = self.models['scaler'].transform(features)
                print("[INFO] Applied scaler preprocessing")
            except Exception as e:
                print(f"[WARNING] Scaler transform failed: {e}")

        # Get the main model (example_model or first available)
        if self.demo_mode:
            model = self.models['demo']
        elif 'example_model' in self.models:
            model = self.models['example_model']
        else:
            model = list(self.models.values())[0]

        # Make predictions
        predictions = model.predict(features)

        # Get probabilities if available
        if hasattr(model, 'predict_proba'):
            probabilities = model.predict_proba(features)
        else:
            # Fallback: create dummy probabilities
            probabilities = np.zeros((len(features), 2))
            probabilities[np.arange(len(features)), predictions] = 1.0

        result = {
            "predictions": predictions.tolist(),
            "probabilities": probabilities.tolist(),
            "n_samples": len(features),
            "models_loaded": list(self.models.keys())
        }

        if self.demo_mode:
            result["demo_mode"] = True
            result["message"] = "Using demo model - example models are available"

        return result
'''

        elif model_type == ModelType.PYTORCH:
            template_str = '''"""
Model Implementation - PyTorch Model
"""

import torch
from pathlib import Path


class Model:
    """PyTorch model wrapper"""

    def __init__(self, model_path):
        """
        Initialize model

        Args:
            model_path: Model file path (.pth or .pt file)
        """
        self.model_path = model_path
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = self.load_model()

    def load_model(self):
        """Load model"""
        try:
            # If dict format (containing model_file etc.)
            if isinstance(self.model_path, dict):
                model_file = self.model_path.get('modelFile')
            else:
                model_file = self.model_path

            model = torch.load(model_file, map_location=self.device)
            model.eval()
            print(f"Model loaded successfully from {model_file}")
            return model
        except Exception as e:
            print(f"Error loading model: {e}")
            raise

    def predict(self, data: dict):
        """
        Prediction method

        Args:
            data: Input data dictionary

        Returns:
            Prediction result dictionary
        """
        # TODO: Implement your prediction logic
        raise NotImplementedError("Please implement the predict() method")
'''

        else:
            # Default generic template
            template_str = '''"""
Model Implementation - {{ model_type }} Model
"""


class Model:
    """Model wrapper"""

    def __init__(self, model_path):
        """
        Initialize model

        Args:
            model_path: Model file path
        """
        self.model_path = model_path
        self.model = self.load_model()

    def load_model(self):
        """Load model"""
        # TODO: Implement model loading logic
        raise NotImplementedError("Please implement the load_model() method")

    def predict(self, data: dict):
        """
        Prediction method

        Args:
            data: Input data dictionary

        Returns:
            Prediction result dictionary
        """
        # TODO: Implement your prediction logic
        raise NotImplementedError("Please implement the predict() method")
'''

        return Template(template_str)

    def _get_dockerfile_content(self) -> str:
        """Get Dockerfile content"""
        # Generate different Python version requirements based on model type
        python_version = "3.10"
        if ModelType.PYTORCH in self.model_types or ModelType.RECBOLE in self.model_types:
            python_version = "3.10"
        else:
            python_version = "3.8"

        return f'''FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.5.14 /uv /uvx /bin/

WORKDIR /service
COPY /service .
COPY env.json .

# Install system dependencies
RUN apt-get update && apt-get -y install gcc wget && rm -rf /var/lib/apt/lists/*

# Install grpc_health_probe for Kubernetes health checks
RUN wget -qO/bin/grpc_health_probe https://github.com/grpc-ecosystem/grpc-health-probe/releases/download/v0.4.19/grpc_health_probe-linux-amd64 && \\
    chmod +x /bin/grpc_health_probe

# Create virtual environment
RUN uv venv /opt/venv --python {python_version}

# Install Python dependencies
RUN . /opt/venv/bin/activate && uv pip install -r requirements.txt

ENV PORT=50051
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Default command (can be overridden in docker-compose)
CMD ["/opt/venv/bin/python", "/service/main.py"]
'''

    def _get_docker_compose_content(self) -> str:
        """Get docker-compose.yml content"""
        # Simple mode: single service
        services_yaml = f'''  {self.project_name}-grpc:
    build:
      context: .
      dockerfile: Dockerfile-grpc
    working_dir: /service
    ports:
      - "50051:50051"
    volumes:
      - ./service:/service
      - ./env.json:/service/env.json
      - ./models:/models  # Mount models directory
    command: /opt/venv/bin/python /service/main.py
    networks:
      - backend'''

        return f'''version: '3.8'

services:
{services_yaml}

networks:
  backend:
    driver: bridge
'''

    # ========== New Methods: Config Parser and Base Class Generation ==========

    def _generate_config_parser(self):
        """Generate grpc_config.py configuration parser"""
        grpc_package_dir = self.project_path / "service" / "grpc_package"

        config_parser_content = '''"""
gRPC Config Parser - Configuration Parser

Features:
- Template variable replacement (${company}, ${version})
- modelPaths parsing
- Configuration validation
"""

import json
from pathlib import Path
from typing import Dict, Any


class ConfigParser:
    """Configuration parser"""

    def __init__(self, config_path: str = "env.json"):
        """
        Initialize configuration parser

        Args:
            config_path: Configuration file path
        """
        self.config_path = self._find_config_file(config_path)
        self.config = self._load_config()

    def _find_config_file(self, config_path: str) -> Path:
        """
        Find configuration file by searching current and parent directories

        Args:
            config_path: Configuration file path

        Returns:
            Path to configuration file

        Raises:
            FileNotFoundError: If configuration file not found
        """
        path = Path(config_path)

        # If absolute path or exists in current directory, use it directly
        if path.is_absolute() or path.exists():
            return path

        # Search in current directory and parent directories (up to 5 levels)
        current_dir = Path.cwd()
        for _ in range(5):
            candidate = current_dir / config_path
            if candidate.exists():
                return candidate

            # Move to parent directory
            parent = current_dir.parent
            if parent == current_dir:  # Reached root
                break
            current_dir = parent

        # Not found, return original path (will raise error in _load_config)
        return path

    def _load_config(self) -> Dict:
        """Load configuration file"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file does not exist: {self.config_path}")

        with open(self.config_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def get_server_config(self, company: str = None) -> Dict:
        """
        Get server configuration

        Args:
            company: Company code (required for multi-company mode)

        Returns:
            Server configuration dictionary
        """
        grpc_config = self.config.get('grpc', {})

        if company:
            # Multi-company mode
            return grpc_config.get('servers', {}).get(company, {})
        else:
            # Single server mode
            return grpc_config.get('server', {})

    def get_model_paths(self, company: str, version: str) -> Dict:
        """
        Get model paths configuration (supports Manifest mode)

        Args:
            company: Company code
            version: Version number

        Returns:
            Model paths dictionary (with template variables replaced)
        """
        server_config = self.get_server_config(company)
        model_paths_config = server_config.get('modelPaths', {}).get(version, {})

        # Check if Manifest mode
        if isinstance(model_paths_config, dict) and model_paths_config.get('mode') == 'manifest':
            # Manifest mode: load models from manifest.json
            base_path = model_paths_config.get('base_path', '')

            # Replace template variables in base_path
            base_path = self._replace_variables(base_path, company, version)

            # Import ManifestLoader
            try:
                import sys
                from pathlib import Path

                # Add grpc_package to path if needed
                grpc_package_path = Path(__file__).parent
                if str(grpc_package_path) not in sys.path:
                    sys.path.insert(0, str(grpc_package_path))

                from grpc_manifest_loader import ManifestLoader

                # Load models from manifest
                loader = ManifestLoader(base_path)
                model_paths = loader.load()

                print(f"[INFO] Loaded {len(model_paths)} models from manifest in {base_path}")
                return model_paths

            except ImportError as e:
                print(f"[WARNING] ManifestLoader not available: {e}")
                print(f"[WARNING] Falling back to empty model paths")
                return {}
            except Exception as e:
                print(f"[ERROR] Failed to load manifest: {e}")
                raise
        else:
            # Explicit path mode: return paths directly
            return self._replace_variables(model_paths_config, company, version)

    def _replace_variables(self, value: Any, company: str, version: str) -> Any:
        """
        Replace template variables

        Supported variables:
        - ${company}: Company code
        - ${version}: Version number
        - ${environment}: Environment

        Args:
            value: Value to replace
            company: Company code
            version: Version number

        Returns:
            Replaced value
        """
        environment = self.config.get('environment', 'local')

        if isinstance(value, str):
            return value.replace('${company}', company) \\
                       .replace('${version}', version) \\
                       .replace('${environment}', environment)
        elif isinstance(value, dict):
            return {k: self._replace_variables(v, company, version) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._replace_variables(v, company, version) for v in value]
        else:
            return value

    def get_grpc_options(self, company: str = None) -> Dict:
        """Get gRPC options configuration"""
        server_config = self.get_server_config(company)
        return server_config.get('grpcOptions', {})

    def get_sentry_config(self) -> Dict:
        """Get Sentry configuration"""
        return self.config.get('grpc', {}).get('sentry', {})
'''

        (grpc_package_dir / "grpc_config.py").write_text(config_parser_content)

    def _generate_base_classes(self):
        """Generate base class templates"""
        grpc_package_dir = self.project_path / "service" / "grpc_package"
        base_dir = grpc_package_dir / "base"
        base_dir.mkdir(exist_ok=True)
        (base_dir / "__init__.py").touch()

        # 1. BaseClassifier - Base classifier class
        base_classifier_content = '''"""
BaseClassifier - Classifier Model Base Class

For classification tasks with multiple model files

Features:
- Supports multiple model files (features_min_max, scaler, rfc_model, catb_model, etc.)
- modelPaths dictionary format
- Batch model loading
- Manifest-based auto-discovery (NEW)
"""

import joblib
import os
from typing import Dict, Union
from pathlib import Path
import sys

# Import ManifestLoader
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from grpc_manifest_loader import ManifestLoader, ManifestError
except ImportError:
    ManifestLoader = None
    ManifestError = Exception


class BaseClassifier:
    """Base classifier"""

    def __init__(self, model_path_config: Union[Dict[str, str], Dict]):
        """
        Initialize classifier

        Args:
            model_path_config: Model configuration, supports two modes:

            Mode 1 - Explicit paths (backward compatible):
                {
                    "features_min_max_model": "/path/to/features_min_max.pkl",
                    "scaler_model": "/path/to/scaler.pkl",
                    "rfc_model": "/path/to/rfc_model.pkl"
                }

            Mode 2 - Manifest-based (NEW):
                {
                    "mode": "manifest",
                    "base_path": "/models/trial/alc3/"
                }
        """
        self.model_path_config = model_path_config
        self.model_path_dict = self._resolve_model_paths()
        self.models = self.load_models()

    def _resolve_model_paths(self) -> Dict[str, str]:
        """
        Resolve model paths from configuration

        Returns:
            Model file path dictionary
        """
        # Check if manifest mode
        if isinstance(self.model_path_config, dict) and self.model_path_config.get('mode') == 'manifest':
            return self._load_from_manifest()
        else:
            # Explicit paths mode (backward compatible)
            return self.model_path_config

    def _load_from_manifest(self) -> Dict[str, str]:
        """Load model paths from manifest.json"""
        if ManifestLoader is None:
            raise ImportError("ManifestLoader not available. Please check grpc_manifest_loader.py")

        base_path = self.model_path_config.get('base_path')
        if not base_path:
            raise ValueError("'base_path' is required for manifest mode")

        print(f"[INFO] Loading models from manifest in: {base_path}")

        try:
            loader = ManifestLoader(base_path)
            model_paths = loader.load()

            # Print metadata
            metadata = loader.get_metadata()
            print(f"[INFO] Manifest version: {metadata.get('model_version')}")
            print(f"[INFO] Created at: {metadata.get('created_at')}")

            return model_paths
        except ManifestError as e:
            print(f"[ERROR] Manifest error: {e}")
            raise
        except Exception as e:
            print(f"[ERROR] Failed to load manifest: {e}")
            raise

    def load_models(self) -> Dict:
        """Batch load model files"""
        models = {}
        for model_name, model_path in self.model_path_dict.items():
            if os.path.exists(model_path):
                try:
                    models[model_name] = joblib.load(model_path)
                    print(f"[OK] Loaded model: {model_name} from {model_path}")
                except Exception as e:
                    print(f"[ERROR] Load failed: {model_name} - {str(e)}")
                    raise
            else:
                print(f"[ERROR] File not found: {model_path}")
                raise FileNotFoundError(f"Model file does not exist: {model_path}")

        return models

    def predict(self, data: dict) -> dict:
        """
        Prediction method (subclasses must implement)

        Args:
            data: Input data dictionary

        Returns:
            Prediction result dictionary
        """
        raise NotImplementedError("Subclass must implement predict() method")
'''

        (base_dir / "base_classifier.py").write_text(base_classifier_content, encoding='utf-8')

        # 2. BaseRecommender - Base recommender class for recommendation systems
        base_recommender_content = '''"""
BaseRecommender - Recommender System Model Base Class

For recommendation tasks with single model file

Features:
- Single model file
- modelPaths dictionary format (model_file)
- RankFM and other recommendation algorithm support
- Manifest-based auto-discovery (NEW)
"""

import pickle
from typing import Dict, Union
from pathlib import Path
import sys
from sentry_sdk import capture_exception

# Import ManifestLoader
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from grpc_manifest_loader import ManifestLoader, ManifestError
except ImportError:
    ManifestLoader = None
    ManifestError = Exception


class BaseRecommender:
    """Base recommender system"""

    def __init__(self, model_path_config: Union[Dict[str, str], Dict]):
        """
        Initialize recommendation model

        Args:
            model_path_config: Model configuration, supports two modes:

            Mode 1 - Explicit paths (backward compatible):
                {"model_file": "/path/to/model.pkl"}

            Mode 2 - Manifest-based (NEW):
                {
                    "mode": "manifest",
                    "base_path": "/models/trial/ape4/"
                }
        """
        self.model_path_config = model_path_config
        self.model_path_dict = self._resolve_model_paths()

        # Get the main model file path
        self.model_path = self.model_path_dict.get('model_file')
        if not self.model_path:
            raise ValueError("Missing 'model_file' key in model paths")

        self.model = self.load_model()

    def _resolve_model_paths(self) -> Dict[str, str]:
        """
        Resolve model paths from configuration

        Returns:
            Model file path dictionary
        """
        # Check if manifest mode
        if isinstance(self.model_path_config, dict) and self.model_path_config.get('mode') == 'manifest':
            return self._load_from_manifest()
        else:
            # Explicit paths mode (backward compatible)
            return self.model_path_config

    def _load_from_manifest(self) -> Dict[str, str]:
        """Load model paths from manifest.json"""
        if ManifestLoader is None:
            raise ImportError("ManifestLoader not available. Please check grpc_manifest_loader.py")

        base_path = self.model_path_config.get('base_path')
        if not base_path:
            raise ValueError("'base_path' is required for manifest mode")

        print(f"[INFO] Loading models from manifest in: {base_path}")

        try:
            loader = ManifestLoader(base_path)
            model_paths = loader.load()

            # Print metadata
            metadata = loader.get_metadata()
            print(f"[INFO] Manifest version: {metadata.get('model_version')}")
            print(f"[INFO] Created at: {metadata.get('created_at')}")

            return model_paths
        except ManifestError as e:
            print(f"[ERROR] Manifest error: {e}")
            raise
        except Exception as e:
            print(f"[ERROR] Failed to load manifest: {e}")
            raise

    def load_model(self):
        """Load model"""
        try:
            with open(self.model_path, "rb") as pickle_file:
                model = pickle.load(pickle_file)
            print(f"[OK] Loaded model: {self.model_path}")
            return model
        except Exception as e:
            print(f"[ERROR] Load failed: {self.model_path} - {str(e)}")
            raise

    def predict(self, data: dict) -> list:
        """
        Prediction method (subclasses can override)

        Args:
            data: Input data dictionary
                For example: {"X": "item_id", "N": 10}

        Returns:
            Recommendation result list
        """
        result = []
        try:
            # Default implementation: RankFM recommendation
            data_X = data.get("X")
            data_N = int(data.get("N", 10))

            if hasattr(self.model, 'similar_items'):
                result = self.model.similar_items(data_X, data_N).tolist()
            else:
                raise NotImplementedError("Model does not have similar_items method")

        except AssertionError as e:
            print(f"Assertion error: {str(e)}")
        except Exception as e:
            print(f"Prediction error: {str(e)}")
            capture_exception(e)

        return result
'''

        (base_dir / "base_recommender.py").write_text(base_recommender_content, encoding='utf-8')

    def _generate_test_client(self):
        """Generate test client script"""
        # Simple mode: fixed port
        port = "50051"

        test_client_content = f'''"""
Test Client for gRPC ML Service

This is a simple test client to verify the gRPC service is working correctly.
"""

import grpc
from service.proto import grpc_pb2, grpc_pb2_grpc
from google.protobuf import struct_pb2

service_api = 'localhost:{port}'
def test_predict():
    """Test the Predict RPC method"""
    # Connect to the gRPC server
    channel = grpc.insecure_channel(service_api)
    stub = grpc_pb2_grpc.MLStub(channel)

    # Test Case 1: Simple feature list
    print("=" * 60)
    print("Test Case 1: Feature list format")
    print("=" * 60)

    request_data = struct_pb2.Struct()
    request_data.update({{
        "features": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    }})

    request = grpc_pb2.MLRequest(request=request_data)

    try:
        response = stub.Predict(request)
        print("[OK] Request sent successfully")
        print(f"Response: {{response.response}}")
        print()
    except grpc.RpcError as e:
        print(f"[ERROR] RPC failed: {{e.code()}}")
        print(f"Details: {{e.details()}}")
        return

    # Test Case 2: Feature dictionary format
    print("=" * 60)
    print("Test Case 2: Feature dictionary format")
    print("=" * 60)

    request_data2 = struct_pb2.Struct()
    request_data2.update({{
        "feature1": 1.5,
        "feature2": 2.5,
        "feature3": 3.5
    }})

    request2 = grpc_pb2.MLRequest(request=request_data2)

    try:
        response2 = stub.Predict(request2)
        print("[OK] Request sent successfully")
        print(f"Response: {{response2.response}}")
        print()
    except grpc.RpcError as e:
        print(f"[ERROR] RPC failed: {{e.code()}}")
        print(f"Details: {{e.details()}}")
        return

    print("=" * 60)
    print("[OK] All tests passed!")
    print("=" * 60)


if __name__ == '__main__':
    print("gRPC ML Service Test Client")
    print("Connecting to " + service_api)
    print()

    try:
        test_predict()
    except Exception as e:
        print(f"✗ Test failed: {{str(e)}}")
        import traceback
        traceback.print_exc()
'''

        test_client_file = self.project_path / "test_client.py"
        test_client_file.write_text(test_client_content, encoding='utf-8')

    def _generate_setup_scripts(self):
        """Generate setup scripts for quick project initialization"""

        # Generate setup.sh for Linux/Mac
        setup_sh_content = f'''#!/bin/bash
# Quick setup script for {self.project_name}

set -e  # Exit on error

echo "=================================="
echo "  {self.project_name} Setup"
echo "=================================="
echo ""

# 1. Find and copy environment configuration
echo "1. Finding environment configuration..."
# Search for env.json in parent directories (up to 4 levels)
ENV_PATH=""
for i in . .. ../.. ../../.. ../../../..; do
    if [ -f "$i/env.sample.json" ]; then
        ENV_PATH="$i"
        break
    fi
done

if [ -z "$ENV_PATH" ]; then
    echo "   [WARNING] env.sample.json not found in parent directories"
    echo "   Please ensure env.json exists in project root"
else
    if [ ! -f "$ENV_PATH/env.json" ]; then
        cp "$ENV_PATH/env.sample.json" "$ENV_PATH/env.json"
        echo "   * env.json created at $ENV_PATH"
    else
        echo "   i env.json already exists at $ENV_PATH"
    fi
fi

# 2. Create models directory
echo ""
echo "2. Creating models directory structure..."
mkdir -p models
echo "   ✓ models directory created"

# 3. Install Python dependencies
echo ""
echo "3. Installing Python dependencies..."
pip install -r service\/requirements.txt
echo "   ✓ Dependencies installed"

# 4. Compile proto files
echo ""
echo "4. Compiling proto files..."
cd service
python -m grpc_tools.protoc -I../proto --python_out=proto --grpc_python_out=proto ../proto/grpc.proto

# Fix import statement in generated grpc file
sed -i 's/^import grpc_pb2 as grpc__pb2/from . import grpc_pb2 as grpc__pb2/' proto/grpc_pb2_grpc.py

cd ..
echo "   ✓ Proto files compiled"

echo ""
echo "=================================="
echo "  Setup Complete!"
echo "=================================="
echo ""
echo "To start the service:"
echo "  cd service"
echo "  python main.py"
echo ""
echo "To test the service (in another terminal):"
echo "  python test_client.py"
echo ""
'''
        
        setup_sh_file = self.project_path / "setup.sh"
        setup_sh_file.write_text(setup_sh_content, encoding='utf-8')
        # Make it executable on Unix-like systems
        try:
            import stat
            setup_sh_file.chmod(setup_sh_file.stat().st_mode | stat.S_IEXEC)
        except:
            pass  # Skip on Windows
        
        # Generate setup.bat for Windows
        setup_bat_content = f'''@echo off
REM Quick setup script for {self.project_name}

echo ==================================
echo   {self.project_name} Setup
echo ==================================
echo.

REM 1. Find and copy environment configuration
echo 1. Finding environment configuration...
set ENV_PATH=
for %%i in (. .. ..\.. ..\..\.. ..\..\..\..\..) do (
    if exist "%%i\\env.sample.json" (
        set ENV_PATH=%%i
        goto :found
    )
)
:found

if "%ENV_PATH%"=="" (
    echo    [WARNING] env.sample.json not found in parent directories
    echo    Please ensure env.json exists in project root
) else (
    if not exist "%ENV_PATH%\\env.json" (
        copy "%ENV_PATH%\\env.sample.json" "%ENV_PATH%\\env.json"
        echo    * env.json created at %ENV_PATH%
    ) else (
        echo    i env.json already exists at %ENV_PATH%
    )
)

REM 2. Create models directory
echo.
echo 2. Creating models directory structure...
if not exist models mkdir models
echo    * models directory created

REM 3. Install Python dependencies
echo.
echo 3. Installing Python dependencies...
pip install -r service\requirements.txt
echo    * Dependencies installed

REM 4. Compile proto files
echo.
echo 4. Compiling proto files...
cd service
python -m grpc_tools.protoc -I../proto --python_out=proto --grpc_python_out=proto ../proto/grpc.proto

REM Fix import statement in generated grpc file
powershell -Command "(Get-Content proto\grpc_pb2_grpc.py) -replace '^import grpc_pb2 as grpc__pb2', 'from . import grpc_pb2 as grpc__pb2' | Set-Content proto\grpc_pb2_grpc.py"

cd ..
echo    * Proto files compiled

echo.
echo ==================================
echo   Setup Complete!
echo ==================================
echo.
echo To start the service:
echo   cd service
echo   python main.py
echo.
echo To test the service (in another terminal):
echo   python test_client.py
echo.
pause
'''
        
        setup_bat_file = self.project_path / "setup.bat"
        setup_bat_file.write_text(setup_bat_content, encoding='utf-8')

    def _generate_dummy_models_script(self):
        """Generate script to create dummy model files for testing"""

        # Build model paths list based on companies and versions
        # Generate ALC-style model files (classification models)
        model_paths_list = []
        for company in self.companies:
            for version in self.versions:
                model_paths_list.append(f'        "models/{company}/{version}/features_min_max.pkl",')
                model_paths_list.append(f'        "models/{company}/{version}/scaler.pkl",')
                model_paths_list.append(f'        "models/{company}/{version}/catb_model.pkl",')

        models_list = '\n'.join(model_paths_list)

        script_content = f'''#!/usr/bin/env python3
"""
Generate dummy model files for testing

This script creates simple pickle files that can be used for testing the gRPC service.
"""

import pickle
from pathlib import Path


class DummyModel:
    """A simple dummy model for testing purposes"""

    def __init__(self, model_name="dummy_model", version="v1"):
        self.model_name = model_name
        self.version = version
        self.trained = True

    def predict(self, data):
        """Dummy predict method"""
        return {{"prediction": "dummy_result", "confidence": 0.95}}

    def __repr__(self):
        return f"DummyModel(name={{self.model_name}}, version={{self.version}})"


def generate_dummy_model(output_path: Path):
    """Generate a dummy model file"""
    # Create parent directories if they don't exist
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Create dummy model
    model = DummyModel()

    # Save as pickle file
    with open(output_path, 'wb') as f:
        pickle.dump(model, f)

    print(f"[OK] Generated dummy model: {{output_path}}")


def main():
    """Generate all required dummy models based on env.json"""
    print("=" * 50)
    print("  Generating Dummy Model Files")
    print("=" * 50)
    print()

    # Define model paths based on env.json configuration
    models_to_generate = [
{models_list}
    ]

    for model_path in models_to_generate:
        output_path = Path(model_path)
        generate_dummy_model(output_path)

    print()
    print("=" * 50)
    print("  All dummy models generated successfully!")
    print("=" * 50)
    print()
    print("Note: These are dummy models for testing only.")
    print("Replace them with your actual trained models before deployment.")


if __name__ == "__main__":
    main()
'''

        script_file = self.project_path / "generate_dummy_models.py"
        script_file.write_text(script_content, encoding='utf-8')

    def _generate_gitignore(self):
        """Generate .gitignore file"""
        gitignore_content = '''# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
env/
venv/
ENV/
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# IDE
.vscode/
.idea/
*.swp
*.swo
*~
.DS_Store

# Project specific
env.json
*.pkl
*.pth
*.pt
*.h5
*.pb
models/*
!models/.gitkeep

# Proto compiled files
service/proto/*_pb2.py
service/proto/*_pb2_grpc.py
service/proto/*_pb2.pyi

# Logs
*.log
logs/

# Docker
.env

# Testing
.pytest_cache/
.coverage
htmlcov/
'''
        
        gitignore_file = self.project_path / ".gitignore"
        gitignore_file.write_text(gitignore_content, encoding='utf-8')

    def _generate_example_models_with_manifest(self):
        """
        Generate example model files with manifest.json (NEW)

        This method creates:
        1. Simple sklearn models for testing
        2. manifest.json for each model directory
        3. Ensures models are compatible with Manifest mode
        """
        import pickle
        import json
        from datetime import datetime

        if not self.silent:
            generator_logger.info("Generating example models with manifest...")

        # For each company and version, generate models
        for company in self.companies:
            for version in self.versions:
                model_dir = self.project_path / "models" / company / version
                model_dir.mkdir(parents=True, exist_ok=True)

                # Generate example models
                models_info = self._create_example_models(model_dir, company, version)

                # Generate manifest.json
                self._create_manifest_file(model_dir, version, models_info)

                if not self.silent:
                    generator_logger.info(f"  ✓ Generated models and manifest for {company}/{version}")

    def _create_example_models(self, model_dir: Path, company: str, version: str) -> dict:
        """
        Create example model files

        Returns:
            dict: Model information for manifest
        """
        import pickle
        import numpy as np

        models_info = {}

        # Create a simple sklearn LogisticRegression model
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler

            # Create and train a simple model
            X_train = np.random.rand(100, 5)
            y_train = np.random.randint(0, 2, 100)

            model = LogisticRegression(random_state=42)
            model.fit(X_train, y_train)

            # Save model
            model_file = model_dir / "example_model.pkl"
            with open(model_file, 'wb') as f:
                pickle.dump(model, f)

            models_info["example_model"] = {
                "file": "example_model.pkl",
                "required": True,
                "size_mb": round(model_file.stat().st_size / (1024 * 1024), 2),
                "type": "sklearn.LogisticRegression"
            }

            # Create a scaler
            scaler = StandardScaler()
            scaler.fit(X_train)

            scaler_file = model_dir / "scaler.pkl"
            with open(scaler_file, 'wb') as f:
                pickle.dump(scaler, f)

            models_info["scaler"] = {
                "file": "scaler.pkl",
                "required": True,
                "size_mb": round(scaler_file.stat().st_size / (1024 * 1024), 2),
                "type": "sklearn.StandardScaler"
            }

        except ImportError:
            # Fallback: create dummy models if sklearn not available
            generator_logger.warning("sklearn not available, creating dummy models")

            dummy_model = {"type": "dummy", "version": version}

            model_file = model_dir / "example_model.pkl"
            with open(model_file, 'wb') as f:
                pickle.dump(dummy_model, f)

            models_info["example_model"] = {
                "file": "example_model.pkl",
                "required": True,
                "size_mb": round(model_file.stat().st_size / (1024 * 1024), 2),
                "type": "dummy"
            }

        return models_info

    def _create_manifest_file(self, model_dir: Path, version: str, models_info: dict):
        """
        Create manifest.json file

        Args:
            model_dir: Model directory path
            version: Version number
            models_info: Model information dictionary
        """
        import json
        from datetime import datetime

        manifest = {
            "version": "1.0",
            "model_version": version,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "description": f"Example models for {version}",
            "metadata": {
                "framework": "sklearn",
                "python_version": "3.8+",
                "generated_by": "aigear-grpc",
                "note": "These are example models for testing. Replace with your trained models."
            },
            "models": {}
        }

        # Add model information
        for model_name, model_info in models_info.items():
            manifest["models"][model_name] = {
                "file": model_info["file"],
                "required": model_info["required"],
                "size_mb": model_info.get("size_mb", 0),
                "type": model_info.get("type", "unknown")
            }

        # Write manifest.json
        manifest_file = model_dir / "manifest.json"
        with open(manifest_file, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        if not self.silent:
            generator_logger.info(f"    ✓ Created manifest.json with {len(models_info)} models")

    def _create_models_directory(self):
        """Create models directory structure"""
        models_dir = self.project_path / "models"
        models_dir.mkdir(exist_ok=True)

        # Simple mode: flat structure, no subdirectories
        # Just create .gitkeep to track the directory
        (models_dir / ".gitkeep").touch()
        
        # Create README in models directory
        models_readme = '''# Models Directory

Place your trained model files here.

## Directory Structure

Simple flat structure:
```
models/
├── model.pkl
├── scaler.pkl
└── other_model_files...
```

## Configuration

Model file paths are configured in `env.json`:

```json
"modelPaths": {
  "model_file": "/models/model.pkl",
  "scaler_file": "/models/scaler.pkl"
}
```

Or use manifest mode (recommended):

```json
"modelPaths": {
  "mode": "manifest",
  "base_path": "/models/"
}
```

## Demo Mode

If model files are not found, the service will automatically run in **demo mode**:
- Uses a simple random prediction model
- Allows testing the service without actual model files
- Returns predictions with `demo_mode: true` flag

## Adding Your Models

1. Place your trained model files (`.pkl`, `.pth`, `.h5`, etc.) directly in the models/ directory
2. Update the `env.json` configuration if needed
3. Restart the service

The service will automatically load the real models when they are detected.
'''

        (models_dir / "README.md").write_text(models_readme, encoding='utf-8')
