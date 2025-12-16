"""
gRPC Service Generator - Automatically generate gRPC ML service projects

This module provides the ability to automatically generate gRPC services
similar to ALC and Macaron style.

Generated projects include:
- Standard gRPC proto definitions
- Dynamic model loading system
- Unified logging system
- Docker containerization configuration
- Environment configuration management

Usage example:
    generator = GrpcServiceGenerator(
        project_name="my_service",
        service_template=ServiceTemplate.MULTI_COMPANY,
        model_types=[ModelType.SKLEARN, ModelType.CATBOOST]
    )
    generator.generate()
"""

from pathlib import Path
from typing import List, Dict, Optional
from enum import Enum
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
    SIMPLE = "simple"                       # Single model, single company
    MULTI_VERSION = "multi_version"         # Single company, multiple versions (e.g., ALC3/ALC4)
    MULTI_COMPANY = "multi_company"         # Multiple companies, multiple versions (e.g., Macaron)


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
        service_template: ServiceTemplate = ServiceTemplate.SIMPLE,
        model_types: Optional[List[ModelType]] = None,
        companies: Optional[List[str]] = None,
        versions: Optional[List[str]] = None,
        output_dir: Optional[Path] = None,
        features: Optional[Dict] = None,
        model_files: Optional[Dict] = None
    ):
        """
        Initialize gRPC Service Generator

        Args:
            project_name: Project name
            service_template: Service template type
            model_types: Model type list (default: [SKLEARN])
            companies: Company code list (for MULTI_COMPANY template)
            versions: Version list (for MULTI_VERSION and MULTI_COMPANY templates)
            output_dir: Output directory (default: current directory)
            features: Feature configuration (sentry, health_check, keepalive, etc.)
            model_files: Model file configuration (for generating modelPaths)
        """
        self.project_name = project_name
        self.template_type = service_template
        self.model_types = model_types or [ModelType.SKLEARN]
        self.companies = companies or ["demo"]
        self.versions = versions or ["v1"]
        self.output_dir = output_dir or Path.cwd()

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
        generator_logger.info(f"Starting gRPC service project generation: {self.project_name}")
        generator_logger.info(f"Template type: {self.template_type.value}")
        generator_logger.info(f"Model types: {[m.value for m in self.model_types]}")

        # 1. Create directory structure
        self._create_directory_structure()
        generator_logger.info("✓ Directory structure created")

        # 2. Generate proto files
        self._generate_proto()
        generator_logger.info("✓ Proto files generated")

        # 3. Generate main.py
        self._generate_main()
        generator_logger.info("✓ main.py generated")

        # 4. Generate grpc_package
        self._generate_grpc_package()
        generator_logger.info("✓ grpc_package generated")

        # 4.5. Generate config parser (new)
        self._generate_config_parser()
        generator_logger.info("✓ Config parser generated")

        # 4.6. Generate base classes (new)
        self._generate_base_classes()
        generator_logger.info("✓ Base classes generated")

        # 5. Generate model modules and examples
        self._generate_model_modules()
        generator_logger.info("✓ Model modules generated")

        # 6. Generate configuration files
        self._generate_config()
        generator_logger.info("✓ Configuration files generated")

        # 7. Generate Docker files
        self._generate_docker_files()
        generator_logger.info("✓ Docker files generated")

        # 8. Generate requirements.txt
        self._generate_requirements()
        generator_logger.info("✓ requirements.txt generated")

        # 9. Generate README
        self._generate_readme()
        generator_logger.info("✓ README.md generated")

        # 10. Generate test client (new)
        self._generate_test_client()
        generator_logger.info("✓ Test client generated")

        # 11. Generate setup scripts (new)
        self._generate_setup_scripts()
        generator_logger.info("✓ Setup scripts generated")

        # 12. Generate .gitignore (new)
        self._generate_gitignore()
        generator_logger.info("✓ .gitignore generated")

        # 13. Create models directory structure (new)
        self._create_models_directory()
        generator_logger.info("✓ Models directory created")

        # 14. Print project structure
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
        generator_logger.info(f"   5. python main.py --company {self.companies[0]} --version {self.versions[0]}")
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

        # Create different model directory structures based on template type
        if self.template_type == ServiceTemplate.SIMPLE:
            # Simple template: only one model directory
            model_dir = models_dir / "model"
            model_dir.mkdir(exist_ok=True)
            (model_dir / "__init__.py").touch()

        elif self.template_type == ServiceTemplate.MULTI_VERSION:
            # Multi-version template: version1, version2, ...
            for version in self.versions:
                version_dir = models_dir / version
                version_dir.mkdir(exist_ok=True)
                (version_dir / "__init__.py").touch()

        elif self.template_type == ServiceTemplate.MULTI_COMPANY:
            # Multi-company template: company/version/
            for company in self.companies:
                for version in self.versions:
                    company_version_dir = models_dir / company / version
                    company_version_dir.mkdir(parents=True, exist_ok=True)
                    (company_version_dir / "__init__.py").touch()

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
        # Check if multi-process support is needed
        multi_company = self.template_type == ServiceTemplate.MULTI_COMPANY
        multi_version = self.template_type in [ServiceTemplate.MULTI_VERSION, ServiceTemplate.MULTI_COMPANY]

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
            multi_company=self.template_type == ServiceTemplate.MULTI_COMPANY,
            multi_version=self.template_type in [ServiceTemplate.MULTI_VERSION, ServiceTemplate.MULTI_COMPANY]
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

        # Generate different model files based on template type
        if self.template_type == ServiceTemplate.SIMPLE:
            self._generate_simple_model(models_dir / "model")

        elif self.template_type == ServiceTemplate.MULTI_VERSION:
            for version in self.versions:
                self._generate_simple_model(models_dir / version)

        elif self.template_type == ServiceTemplate.MULTI_COMPANY:
            for company in self.companies:
                for version in self.versions:
                    self._generate_simple_model(models_dir / company / version)

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
        """Generate configuration files"""
        config = self._build_config_structure()

        config_file = self.project_path / "env.sample.json"
        config_file.write_text(json.dumps(config, indent=2, ensure_ascii=False))

    def _build_config_structure(self) -> Dict:
        """Build configuration structure (New standard: using modelPaths)"""
        config = {
            "projectName": self.project_name,
            "environment": "local",
            "grpc": {
                "servers": {}
            }
        }

        # Build different configuration structures based on template type
        if self.template_type == ServiceTemplate.SIMPLE:
            model_paths = self._build_model_paths("demo", "v1")

            config["grpc"]["server"] = {
                "serviceHost": "0.0.0.0",
                "port": "50051",
                "modelPaths": {
                    "v1": model_paths
                },
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
            }

        elif self.template_type == ServiceTemplate.MULTI_VERSION:
            model_paths_dict = {}
            for version in self.versions:
                model_paths_dict[version] = self._build_model_paths("demo", version)

            config["grpc"]["server"] = {
                "serviceHost": "0.0.0.0",
                "port": "50051",
                "modelPaths": model_paths_dict,
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
            }

        elif self.template_type == ServiceTemplate.MULTI_COMPANY:
            for i, company in enumerate(self.companies):
                model_paths_dict = {}
                for version in self.versions:
                    model_paths_dict[version] = self._build_model_paths(company, version)

                config["grpc"]["servers"][company] = {
                    "serviceHost": "0.0.0.0",
                    "port": str(50051 + i),
                    "modelPaths": model_paths_dict,
                    "multiProcessing": {
                        "on": self.features.get('multi_processing', True),
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
                }

        # Add Sentry configuration
        if self.features.get('sentry', True):
            config["grpc"]["sentry"] = {
                "on": False,
                "dsn": "https://your-sentry-dsn@sentry.io/project-id",
                "tracesSampleRate": 1.0
            }

        # Add GKE deployment configuration (optional)
        config["gke"] = {
            "enabled": False,
            "cluster": {
                "name": f"{self.project_name}-cluster",
                "nodeCount": 3,
                "machineType": "e2-standard-4",
                "diskSize": 100,
                "enableAutoscaling": True,
                "minNodes": 1,
                "maxNodes": 10,
                "enableAutopilot": False
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
                    "minReplicas": 2,
                    "maxReplicas": 10,
                    "targetCPU": 70
                }
            },
            "service": {
                "type": "LoadBalancer",
                "port": 50051
            },
            "image": {
                "repository": "grpc-services",
                "tag": "latest"
            },
            "modelStorage": {
                "enabled": False,
                "bucketName": f"{self.project_name}-models"
            }
        }

        # Add GCP project configuration for deployment
        config["gcp"] = {
            "projectId": "",
            "region": "asia-northeast1"
        }

        return config

    def _build_model_paths(self, company: str, version: str) -> Dict:
        """
        Build modelPaths configuration for a single version

        Args:
            company: Company code
            version: Version number

        Returns:
            Model file path dictionary
        """
        # Use custom model_files configuration if available
        if version in self.model_files:
            file_list = self.model_files[version]
        elif 'default' in self.model_files:
            file_list = self.model_files['default']
        else:
            # Default configuration: single model file
            file_list = ['model_file']

        # Generate path dictionary
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
python main.py --company {self.companies[0]} --version {self.versions[0]}
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


def main(company: str{% if multi_version %}, version: str{% endif %}):
    """Main function"""
    logger.info("=" * 60)
    logger.info("Starting gRPC ML Service")
    logger.info(f"Company: {company}")
    {% if multi_version %}
    logger.info(f"Version: {version}")
    {% endif %}
    logger.info("=" * 60)

    # Load configuration using ConfigParser
    config_parser = grpc_config.ConfigParser("env.json")

    # Get server configuration
    server_config = config_parser.get_server_config(company)
    service_host = server_config.get('serviceHost', '0.0.0.0')
    port = server_config.get('port', '50051')

    # Get model paths (modelPaths format)
    {% if multi_version %}
    model_paths = config_parser.get_model_paths(company, version)
    {% else %}
    # For single version, get the first available version
    model_paths_config = server_config.get('modelPaths', {})
    version = list(model_paths_config.keys())[0] if model_paths_config else 'v1'
    model_paths = config_parser.get_model_paths(company, version)
    {% endif %}

    logger.info(f"Model paths configuration: {model_paths}")

    # Get gRPC options
    grpc_options = config_parser.get_grpc_options(company)
    grpc_options['threadCount'] = server_config.get('multiProcessing', {}).get('threadCount', 10)

    # Get multiprocessing configuration
    multi_processing_config = server_config.get('multiProcessing', {})
    is_multi_processing = multi_processing_config.get('on', False)
    process_count = multi_processing_config.get('processCount', 1)

    # Get Sentry configuration
    sentry_config = config_parser.get_sentry_config()
    sentry_enabled = sentry_config.get('on', False)

    if sentry_enabled:
        sentry_init(
            dsn=sentry_config.get('dsn'),
            traces_sample_rate=sentry_config.get('tracesSampleRate', 1.0),
            environment=config_parser.config.get('environment', 'local'),
        )
        logger.info("Sentry enabled")

    # Load model dynamically
    ml_module_instance = grpc_ml_module.MLModule(company{% if multi_version %}, version{% endif %})
    model_class = ml_module_instance.load_module()

    if model_class is None:
        logger.error("Model class not found!")
        sys.exit(1)

    logger.info(f"Model class loaded for: {ml_module_instance.company_code}/{ml_module_instance.version}")

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

    parser = argparse.ArgumentParser(description='gRPC ML Service')
    parser.add_argument('--company', required=True, help='Company code')
    {% if multi_version %}
    parser.add_argument('--version', required=True, help='Model version')
    {% endif %}
    args = parser.parse_args()

    # Check parameters
    if not args.company:
        logger.error("Missing company code!")
        sys.exit(1)
    {% if multi_version %}
    if not args.version:
        logger.error("Missing version!")
        sys.exit(1)
    {% endif %}

    main(args.company{% if multi_version %}, args.version{% endif %})
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

This is a working example with a simple demo model.
Replace with your actual model implementation.
"""

import joblib
import os
import numpy as np
from pathlib import Path


class DemoModel:
    """Simple demo model that returns random predictions (for testing)"""

    def predict(self, X):
        """Return binary predictions (0 or 1)"""
        np.random.seed(42)  # For reproducible results
        n_samples = len(X) if hasattr(X, '__len__') else 1
        return np.random.randint(0, 2, size=n_samples)

    def predict_proba(self, X):
        """Return prediction probabilities"""
        np.random.seed(42)
        n_samples = len(X) if hasattr(X, '__len__') else 1
        # Generate random probabilities that sum to 1
        probs = np.random.dirichlet(np.ones(2), size=n_samples)
        return probs


class Model:
    """Scikit-learn model wrapper with demo mode"""

    def __init__(self, model_paths):
        """
        Initialize model

        Args:
            model_paths: Model file paths dictionary, for example:
                         {"model_file": "/path/to/model.pkl"}
                         or just a string path for single model
        """
        # Handle both dict and string formats
        if isinstance(model_paths, dict):
            self.model_path = model_paths.get('model_file') or list(model_paths.values())[0]
        else:
            self.model_path = model_paths

        self.demo_mode = False
        self.model = self.load_model()

    def load_model(self):
        """Load model (with demo fallback)"""
        if os.path.exists(self.model_path):
            try:
                model = joblib.load(self.model_path)
                print(f"[OK] Model loaded successfully from {self.model_path}")
                return model
            except Exception as e:
                print(f"[WARNING] Error loading model: {e}")
                print("-> Falling back to demo mode")
                self.demo_mode = True
                return self._create_demo_model()
        else:
            print(f"[WARNING] Model file not found: {self.model_path}")
            print("-> Using demo mode for testing")
            self.demo_mode = True
            return self._create_demo_model()

    def _create_demo_model(self):
        """
        Create a simple demo model for testing

        This is a mock model that simulates predictions without requiring
        an actual trained model file.
        """
        return DemoModel()

    def predict(self, data: dict):
        """
        Prediction method

        Args:
            data: Input data dictionary, for example:
                  {
                      "features": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
                  }
                  or
                  {
                      "feature1": 1.0,
                      "feature2": 2.0,
                      "feature3": 3.0
                  }

        Returns:
            Prediction result, for example:
            {
                "predictions": [0, 1],
                "probabilities": [[0.9, 0.1], [0.3, 0.7]],
                "demo_mode": True  # Only present in demo mode
            }
        """
        if self.demo_mode:
            print("[DEMO] Running in DEMO mode - using simulated predictions")

        # Extract features from input
        if 'features' in data:
            # Input format: {"features": [[1, 2, 3], [4, 5, 6]]}
            features = np.array(data['features'])
        else:
            # Input format: {"feature1": 1, "feature2": 2, ...}
            # Convert dict to list
            feature_values = [v for k, v in sorted(data.items()) if k.startswith('feature')]
            if not feature_values:
                # If no 'feature' keys, use all numeric values
                feature_values = [v for v in data.values() if isinstance(v, (int, float))]
            features = np.array([feature_values])

        # Make predictions
        predictions = self.model.predict(features)
        probabilities = self.model.predict_proba(features)

        result = {
            "predictions": predictions.tolist(),
            "probabilities": probabilities.tolist(),
            "n_samples": len(features)
        }

        # Add demo mode indicator
        if self.demo_mode:
            result["demo_mode"] = True
            result["message"] = "Using demo model - replace with actual model file for production"

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
        services = []

        if self.template_type == ServiceTemplate.SIMPLE:
            services.append(f'''  {self.project_name}-grpc:
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
    command: /opt/venv/bin/python /service/main.py --company demo --version v1
    networks:
      - backend''')

        elif self.template_type == ServiceTemplate.MULTI_VERSION:
            for i, version in enumerate(self.versions):
                port = 50051 + i
                services.append(f'''  {self.project_name}-{version}-grpc:
    build:
      context: .
      dockerfile: Dockerfile-grpc
    working_dir: /service
    ports:
      - "{port}:{port}"
    volumes:
      - ./service:/service
      - ./env.json:/service/env.json
      - ./models:/models  # Mount models directory
    command: /opt/venv/bin/python /service/main.py --company demo --version {version}
    environment:
      - PORT={port}
    networks:
      - backend''')

        elif self.template_type == ServiceTemplate.MULTI_COMPANY:
            # For multi-company mode, use the port from env.json config
            for i, company in enumerate(self.companies):
                company_port = str(50051 + i)
                # Create one service per company (not per company-version combination)
                # The company can handle multiple versions internally
                services.append(f'''  {self.project_name}-{company}-grpc:
    build:
      context: .
      dockerfile: Dockerfile-grpc
    working_dir: /service
    ports:
      - "{company_port}:{company_port}"
    volumes:
      - ./service:/service
      - ./env.json:/service/env.json
      - ./models:/models  # Mount models directory
    command: /opt/venv/bin/python /service/main.py --company {company} --version {self.versions[0]}
    environment:
      - PORT={company_port}
    networks:
      - backend''')

        services_yaml = "\n\n".join(services)

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
        self.config_path = Path(config_path)
        self.config = self._load_config()

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
        Get model paths configuration

        Args:
            company: Company code
            version: Version number

        Returns:
            Model paths dictionary (with template variables replaced)
        """
        server_config = self.get_server_config(company)
        model_paths = server_config.get('modelPaths', {}).get(version, {})

        # Replace template variables
        return self._replace_variables(model_paths, company, version)

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

        # 1. BaseClassifier - Base classifier class (ALC style)
        base_classifier_content = '''"""
BaseClassifier - Classifier Model Base Class

For classification tasks with multiple model files (like ALC)

Features:
- Supports multiple model files (features_min_max, scaler, rfc_model, catb_model, etc.)
- modelPaths dictionary format
- Batch model loading
"""

import joblib
import os
from typing import Dict


class BaseClassifier:
    """Base classifier"""

    def __init__(self, model_path_dict: Dict[str, str]):
        """
        Initialize classifier

        Args:
            model_path_dict: Model file path dictionary
                For example: {
                    "features_min_max_model": "/path/to/features_min_max.pkl",
                    "scaler_model": "/path/to/scaler.pkl",
                    "rfc_model": "/path/to/rfc_model.pkl",
                    ...
                }
        """
        self.model_path_dict = model_path_dict
        self.models = self.load_models()

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

        # 2. BaseRecommender - Base recommender class (Macaron style)
        base_recommender_content = '''"""
BaseRecommender - Recommender System Model Base Class

For recommendation tasks with single model file (like Macaron)

Features:
- Single model file
- modelPaths dictionary format (model_file)
- RankFM and other recommendation algorithm support
"""

import pickle
from typing import Dict
from sentry_sdk import capture_exception


class BaseRecommender:
    """Base recommender system"""

    def __init__(self, model_paths: Dict[str, str]):
        """
        Initialize recommendation model

        Args:
            model_paths: Model file path dictionary
                For example: {"model_file": "/path/to/model.pkl"}
        """
        self.model_path = model_paths.get('model_file')
        if not self.model_path:
            raise ValueError("Missing 'model_file' key in model_paths")

        self.model = self.load_model()

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
        # Determine the port based on template type
        if self.template_type == ServiceTemplate.MULTI_COMPANY:
            port = "50051"  # First company's port
        else:
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

# 1. Copy environment configuration
echo "1. Copying environment configuration..."
if [ ! -f env.json ]; then
    cp env.sample.json env.json
    echo "   ✓ env.json created"
else
    echo "   ℹ env.json already exists, skipping"
fi

# 2. Create models directory
echo ""
echo "2. Creating models directory structure..."
mkdir -p models
echo "   ✓ models directory created"

# 3. Install Python dependencies
echo ""
echo "3. Installing Python dependencies..."
pip install -r service/requirements.txt
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
echo "  python main.py --company {self.companies[0]} --version {self.versions[0]}"
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

REM 1. Copy environment configuration
echo 1. Copying environment configuration...
if not exist env.json (
    copy env.sample.json env.json
    echo    * env.json created
) else (
    echo    i env.json already exists, skipping
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
echo   python main.py --company {self.companies[0]} --version {self.versions[0]}
echo.
echo To test the service (in another terminal):
echo   python test_client.py
echo.
pause
'''
        
        setup_bat_file = self.project_path / "setup.bat"
        setup_bat_file.write_text(setup_bat_content, encoding='utf-8')

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

    def _create_models_directory(self):
        """Create models directory structure"""
        models_dir = self.project_path / "models"
        models_dir.mkdir(exist_ok=True)
        
        # Create .gitkeep to track empty directory
        (models_dir / ".gitkeep").touch()
        
        # Create subdirectories based on template type
        if self.template_type == ServiceTemplate.MULTI_COMPANY:
            for company in self.companies:
                for version in self.versions:
                    company_version_dir = models_dir / company / version
                    company_version_dir.mkdir(parents=True, exist_ok=True)
                    (company_version_dir / ".gitkeep").touch()
        
        # Create README in models directory
        models_readme = f'''# Models Directory

Place your trained model files here.

## Directory Structure

{"### Multi-Company Mode" if self.template_type == ServiceTemplate.MULTI_COMPANY else ""}
{"" if self.template_type != ServiceTemplate.MULTI_COMPANY else """
Each company and version has its own directory:
```
models/
├── {}/
│   ├── {}/
│   │   └── model_file.pkl
│   └── {}/
│       └── model_file.pkl
└── {}/
    ├── {}/
    │   └── model_file.pkl
    └── {}/
        └── model_file.pkl
```
""".format(self.companies[0], self.versions[0], self.versions[1] if len(self.versions) > 1 else "v2",
           self.companies[1] if len(self.companies) > 1 else "company2",
           self.versions[0], self.versions[1] if len(self.versions) > 1 else "v2")}

## Configuration

Model file paths are configured in `env.json`:

```json
"modelPaths": {{
  "v1": {{
    "model_file": "/models/company/v1/model_file.pkl"
  }}
}}
```

## Demo Mode

If model files are not found, the service will automatically run in **demo mode**:
- Uses a simple random prediction model
- Allows testing the service without actual model files
- Returns predictions with `demo_mode: true` flag

## Adding Your Models

1. Place your trained model files (`.pkl`, `.pth`, `.h5`, etc.) in the appropriate directory
2. Update the `env.json` configuration if needed
3. Restart the service

The service will automatically load the real models when they are detected.
'''
        
        (models_dir / "README.md").write_text(models_readme, encoding='utf-8')
