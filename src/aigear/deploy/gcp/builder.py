from __future__ import annotations
import time
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Dict, Any, List, Union, Protocol
from google.cloud.devtools import cloudbuild_v1
from google.protobuf import duration_pb2

from .client import get_cloud_build_client, get_project_id
from .errors import (
    CloudBuildError,
    CloudBuildConfigError,
    CloudBuildTimeoutError,
    error_processor
)
from ...common.logger import logger


# Strategy Pattern: Define different source type strategies
class BuildSource(ABC):
    """Abstract base class for build sources"""
    
    @abstractmethod
    def create_source(self) -> cloudbuild_v1.Source:
        """Create Cloud Build source configuration"""
        pass
    
    @abstractmethod
    def get_build_steps(self, config: 'BuildConfig') -> List[cloudbuild_v1.BuildStep]:
        """Get build steps"""
        pass


class DockerfileGenerator:
    """Dockerfile generator"""
    
    @staticmethod
    def generate_python_dockerfile(
        base_image: str = "python:3.9-slim",
        working_dir: str = "/app",
        requirements_file: str = "requirements.txt",
        main_file: str = "app.py",
        port: int = 8080,
        additional_commands: Optional[List[str]] = None
    ) -> str:
        """Generate Dockerfile for Python applications"""
        
        dockerfile_content = f"""# Auto-generated Dockerfile for Python applications
FROM {base_image}

WORKDIR {working_dir}

# Install system dependencies
RUN apt-get update && apt-get install -y \\
    git \\
    curl \\
    && rm -rf /var/lib/apt/lists/*

# Ensure pip is up to date
RUN pip install --upgrade pip

# Copy requirements.txt (will be auto-generated if not exists)
COPY {requirements_file} ./

# Install Python dependencies
RUN pip install --no-cache-dir -r {requirements_file}

# Copy application code
COPY . .

"""
        
        # Add additional commands
        if additional_commands:
            for cmd in additional_commands:
                dockerfile_content += f"RUN {cmd}\n"
        
        dockerfile_content += f"""
# Expose port
EXPOSE {port}

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \\
    CMD curl -f http://localhost:{port}/health || exit 1

# Start command - prefer gunicorn, fallback to python
CMD if [ -f "{main_file}" ]; then \\
        if command -v gunicorn >/dev/null 2>&1; then \\
            gunicorn --bind 0.0.0.0:{port} --workers 2 {main_file.replace('.py', '')}:app; \\
        else \\
            python {main_file}; \\
        fi; \\
    else \\
        echo "No main application file found. Starting a simple server..."; \\
        python -c "from flask import Flask; app = Flask(__name__); app.route('/')(lambda: 'Hello from auto-generated app!'); app.run(host='0.0.0.0', port={port})"; \\
    fi
"""
        
        return dockerfile_content
    
    @staticmethod
    def generate_nodejs_dockerfile(
        node_version: str = "18-alpine",
        working_dir: str = "/app",
        port: int = 3000
    ) -> str:
        """Generate Dockerfile for Node.js applications"""
        
        return f"""# Auto-generated Dockerfile
FROM node:{node_version}

WORKDIR {working_dir}

# Copy package.json and package-lock.json
COPY package*.json ./

# Install dependencies
RUN npm ci --only=production

# Copy application code
COPY . .

# Create non-root user
RUN addgroup -g 1001 -S nodejs
RUN adduser -S nextjs -u 1001

# Change file owner
RUN chown -R nextjs:nodejs {working_dir}
USER nextjs

# Expose port
EXPOSE {port}

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \\
    CMD curl -f http://localhost:{port}/health || exit 1

# Start application
CMD ["npm", "start"]
"""
    
    @staticmethod
    def generate_generic_dockerfile(
        base_image: str = "ubuntu:20.04",
        working_dir: str = "/app",
        copy_files: List[str] = None,
        run_commands: List[str] = None,
        port: int = 8080,
        cmd: List[str] = None
    ) -> str:
        """Generate generic Dockerfile"""
        
        dockerfile_content = f"""# Auto-generated generic Dockerfile
FROM {base_image}

WORKDIR {working_dir}

# Update package manager
RUN apt-get update && apt-get install -y \\
    curl \\
    wget \\
    git \\
    && rm -rf /var/lib/apt/lists/*

"""
        
        # Copy files
        if copy_files:
            for file_pattern in copy_files:
                dockerfile_content += f"COPY {file_pattern} ./\n"
        else:
            dockerfile_content += "COPY . .\n"
        
        # Execute commands
        if run_commands:
            for cmd in run_commands:
                dockerfile_content += f"RUN {cmd}\n"
        
        dockerfile_content += f"""
# Expose port
EXPOSE {port}

"""
        
        # Start command
        if cmd:
            cmd_str = ' '.join(f'"{c}"' for c in cmd)
            dockerfile_content += f"CMD [{cmd_str}]\n"
        else:
            dockerfile_content += 'CMD ["echo", "Hello from auto-generated Dockerfile!"]\n'
        
        return dockerfile_content


class LocalSource(BuildSource):
    """Local source strategy"""
    
    def __init__(self, source_path: Path, project_id: str):
        self.source_path = source_path
        self.project_id = project_id
    
    def create_source(self) -> cloudbuild_v1.Source:
        return cloudbuild_v1.Source(
            storage_source=cloudbuild_v1.StorageSource(
                bucket=f"{self.project_id}_cloudbuild",
                object_="source.tar.gz"
            )
        )
    
    def get_build_steps(self, config: 'BuildConfig') -> List[cloudbuild_v1.BuildStep]:
        docker_args = [
            "build",
            "-f", config.dockerfile,
            "-t", config.get_full_image_name(),
            "."
        ]
        
        # Add extra tags
        if config.tags:
            for tag in config.tags:
                docker_args.extend(["-t", f"{config.get_base_image_name()}:{tag}"])
        
        return [
            cloudbuild_v1.BuildStep(
                name="gcr.io/cloud-builders/docker",
                args=docker_args
            )
        ]


class GcsSource(BuildSource):
    """GCS source strategy"""
    
    def __init__(self, gcs_source: str):
        self.gcs_source = gcs_source
    
    def create_source(self) -> cloudbuild_v1.Source:
        if not self.gcs_source.startswith("gs://"):
            raise CloudBuildConfigError("GCS source must start with 'gs://'")
        
        bucket_object = self.gcs_source[5:]  # Remove "gs://"
        if "/" not in bucket_object:
            raise CloudBuildConfigError("Invalid GCS source format")
        
        bucket, object_path = bucket_object.split("/", 1)
        return cloudbuild_v1.Source(
            storage_source=cloudbuild_v1.StorageSource(
                bucket=bucket,
                object_=object_path
            )
        )
    
    def get_build_steps(self, config: 'BuildConfig') -> List[cloudbuild_v1.BuildStep]:
        docker_args = [
            "build",
            "-f", config.dockerfile,
            "-t", config.get_full_image_name(),
            "."
        ]
        
        if config.tags:
            for tag in config.tags:
                docker_args.extend(["-t", f"{config.get_base_image_name()}:{tag}"])
        
        return [
            cloudbuild_v1.BuildStep(
                name="gcr.io/cloud-builders/docker",
                args=docker_args
            )
        ]


class GitHubSource(BuildSource):
    """GitHub source strategy"""
    
    def __init__(self, repo_url: str, branch: Optional[str] = "main", 
                 commit_sha: Optional[str] = None, context_dir: str = ".",
                 auto_generate_dockerfile: bool = False,
                 dockerfile_type: str = "python"):
        self.repo_url = repo_url
        self.branch = branch
        self.commit_sha = commit_sha
        self.context_dir = context_dir
        self.auto_generate_dockerfile = auto_generate_dockerfile
        self.dockerfile_type = dockerfile_type
    
    def create_source(self) -> Optional[cloudbuild_v1.Source]:
        # GitHub sources don't need predefined source, they clone through steps
        return None
    
    def get_build_steps(self, config: 'BuildConfig') -> List[cloudbuild_v1.BuildStep]:
        steps = []
        
        # Step 1: Clone repository
        clone_args = ["clone", "--depth", "1"]
        if self.branch and not self.commit_sha:
            clone_args += ["--branch", self.branch]
        clone_args += [self.repo_url, "repo"]
        
        steps.append(
            cloudbuild_v1.BuildStep(
                name="gcr.io/cloud-builders/git",
                args=clone_args
            )
        )
        
        # Step 2: Checkout specific commit if needed
        if self.commit_sha:
            steps.extend([
                cloudbuild_v1.BuildStep(
                    name="gcr.io/cloud-builders/git",
                    args=["-C", "repo", "fetch", "origin", self.commit_sha, "--depth", "1"]
                ),
                cloudbuild_v1.BuildStep(
                    name="gcr.io/cloud-builders/git",
                    args=["-C", "repo", "checkout", self.commit_sha]
                )
            ])
        
        # Step 3: Generate Dockerfile if needed
        if self.auto_generate_dockerfile:
            dockerfile_content = self._generate_dockerfile_content()
            
            # Use bash step to create Dockerfile and necessary files
            steps.append(
                cloudbuild_v1.BuildStep(
                    name="gcr.io/cloud-builders/gcloud",
                    entrypoint="bash",
                    args=[
                        "-c",
                        f"""# Create Dockerfile
cat > repo/{config.dockerfile} << 'DOCKERFILE_EOF'
{dockerfile_content}
DOCKERFILE_EOF

# Check and create requirements.txt (if not exists)
if [ ! -f "repo/requirements.txt" ]; then
    echo "📦 Creating basic requirements.txt"
    cat > repo/requirements.txt << 'REQUIREMENTS_EOF'
# Basic Python dependencies
flask>=2.0.0
gunicorn>=20.0.0
requests>=2.25.0
REQUIREMENTS_EOF
else
    echo "✅ requirements.txt already exists"
fi

# Check and create basic app file (if no app.py exists)
if [ ! -f "repo/app.py" ] && [ ! -f "repo/main.py" ]; then
    echo "🐍 Creating basic app.py"
    cat > repo/app.py << 'APP_EOF'
from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    return {{"message": "Hello from auto-generated Flask app!", "status": "running"}}

@app.route('/health')
def health():
    return {{"status": "healthy", "service": "auto-generated-app"}}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
APP_EOF
fi

echo "✅ Auto-generated Dockerfile and dependency files created"
echo "📄 Generated files:"
ls -la repo/Dockerfile repo/requirements.txt repo/app.py 2>/dev/null || true
echo "📋 Dockerfile content preview:"
head -10 repo/{config.dockerfile}
"""
                    ]
                )
            )
        
        # Step 4: Docker build
        context_dir = self.context_dir.strip("/") or "."
        docker_step_dir = f"repo/{context_dir}" if context_dir != "." else "repo"
        
        docker_args = [
            "build",
            "-f", config.dockerfile,
            "-t", config.get_full_image_name(),
        ]
        
        # Add build arguments
        if config.build_args:
            for key, value in config.build_args.items():
                docker_args.extend(["--build-arg", f"{key}={value}"])
                
            # Add default BUILDPLATFORM if not specified
            buildplatform_found = any(key.upper() == "BUILDPLATFORM" for key in config.build_args.keys())
            if not buildplatform_found:
                docker_args.extend(["--build-arg", "BUILDPLATFORM=linux/amd64"])
        else:
            docker_args.extend(["--build-arg", "BUILDPLATFORM=linux/amd64"])
        
        docker_args.append(".")
        
        # Add extra tags
        if config.tags:
            for tag in config.tags:
                base_image = config.get_base_image_name()
                docker_args.extend(["-t", f"{base_image}:{tag}"])
        
        steps.append(
            cloudbuild_v1.BuildStep(
                name="gcr.io/cloud-builders/docker",
                args=docker_args,
                dir=docker_step_dir
            )
        )
        
        return steps
    
    def _generate_dockerfile_content(self) -> str:
        """Generate Dockerfile content based on type"""
        
        if self.dockerfile_type.lower() == "python":
            return DockerfileGenerator.generate_python_dockerfile(
                base_image="python:3.9-slim",
                port=8080,
                additional_commands=[
                    "pip install --upgrade pip",
                    "pip install flask gunicorn"
                ]
            )
        elif self.dockerfile_type.lower() == "nodejs":
            return DockerfileGenerator.generate_nodejs_dockerfile()
        elif self.dockerfile_type.lower() == "generic":
            return DockerfileGenerator.generate_generic_dockerfile(
                base_image="ubuntu:20.04",
                run_commands=[
                    "apt-get update",
                    "apt-get install -y python3 python3-pip",
                    "pip3 install flask"
                ],
                cmd=["python3", "-c", "print('Hello from auto-generated container!')"]
            )
        else:
            # Default simple Dockerfile
            return """# Auto-generated simple Dockerfile
FROM ubuntu:20.04

WORKDIR /app

# Install basic tools
RUN apt-get update && apt-get install -y \\
    curl \\
    wget \\
    python3 \\
    python3-pip \\
    && rm -rf /var/lib/apt/lists/*

# Copy all files
COPY . .

# Install Python dependencies (if exists)
RUN if [ -f requirements.txt ]; then pip3 install -r requirements.txt; fi

# Expose port
EXPOSE 8080

# Default start command
CMD ["python3", "-c", "print('Hello from auto-generated Dockerfile!'); import time; time.sleep(3600)"]
"""


# Builder Pattern: Build configuration
class BuildConfig:
    """Build configuration class"""
    
    def __init__(self, image_name: str, project_id: str):
        self.image_name = image_name
        self.project_id = project_id
        self.dockerfile = "Dockerfile"
        self.timeout_minutes = 10
        self.machine_type = "E2_HIGHCPU_8"
        self.substitutions: Optional[Dict[str, str]] = None
        self.tags: Optional[List[str]] = None
        self.build_args: Optional[Dict[str, str]] = None
    
    def get_base_image_name(self) -> str:
        """Get base image name"""
        if "/" in self.image_name and self.image_name.count("/") >= 2:
            # Already a complete image address
            return self.image_name.rsplit(":", 1)[0] if ":" in self.image_name else self.image_name
        else:
            # Traditional gcr.io format
            return f"gcr.io/{self.project_id}/{self.image_name}"
    
    def get_full_image_name(self) -> str:
        """Get full image name"""
        return self.get_base_image_name()
    
    def get_all_image_names(self) -> List[str]:
        """Get all image names (including tags)"""
        images = [self.get_full_image_name()]
        if self.tags:
            base_image = self.get_base_image_name()
            for tag in self.tags:
                images.append(f"{base_image}:{tag}")
        return images


class CloudBuildBuilder:
    """Cloud Build builder for automated builds (refactored version)"""
    
    def __init__(self, project_id: Optional[str] = None, location: str = "global"):
        """
        Initialize Cloud Build builder
        
        Args:
            project_id: Google Cloud project ID. If None, will be auto-detected
            location: Cloud Build location (e.g., "global", "us-central1")
        """
        self.client = get_cloud_build_client()
        self.project_id = project_id or get_project_id()
        self.location = location
        self.parent_path = f"projects/{self.project_id}/locations/{self.location}"

    def _normalize_machine_type(self, machine_type: Union[str, int, cloudbuild_v1.BuildOptions.MachineType]) -> cloudbuild_v1.BuildOptions.MachineType:
        """Normalize various machine_type inputs to the enum expected by BuildOptions.

        Accepts enum instance, enum name string (e.g., "E2_HIGHCPU_8"), or integer value.
        """
        if isinstance(machine_type, cloudbuild_v1.BuildOptions.MachineType):
            return machine_type
        if isinstance(machine_type, str):
            try:
                return cloudbuild_v1.BuildOptions.MachineType[machine_type]
            except KeyError as exc:
                raise CloudBuildConfigError(
                    f"Invalid machine_type '{machine_type}'. "
                    f"Use one of: {[e.name for e in cloudbuild_v1.BuildOptions.MachineType]}"
                ) from exc
        if isinstance(machine_type, int):
            # Trust caller to pass a valid enum integer
            return machine_type  # type: ignore[return-value]
        raise CloudBuildConfigError("Unsupported machine_type type. Use enum, name string, or integer value.")
        
    def build_from_source(
        self,
        source_path: Path,
        image_name: str,
        dockerfile: str = "Dockerfile",
        timeout_minutes: int = 10,
        machine_type: str = "E2_HIGHCPU_8",
        substitutions: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None,
        **kwargs
    ) -> str:
        """
        Build Docker image from source using Cloud Build
        """
        if not source_path.exists():
            raise CloudBuildConfigError(f"Source path does not exist: {source_path}")
            
        if not (source_path / dockerfile).exists():
            raise CloudBuildConfigError(f"Dockerfile not found: {source_path / dockerfile}")
        
        # Use strategy and builder patterns
        config = BuildConfig(image_name, self.project_id)
        config.dockerfile = dockerfile
        config.timeout_minutes = timeout_minutes
        config.machine_type = machine_type
        config.substitutions = substitutions
        config.tags = tags
        
        source_strategy = LocalSource(source_path, self.project_id)
        
        return self._execute_build(source_strategy, config)
    
    def build_from_gcs(
        self,
        gcs_source: str,
        image_name: str,
        dockerfile: str = "Dockerfile",
        timeout_minutes: int = 10,
        machine_type: str = "E2_HIGHCPU_8",
        substitutions: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None,
        **kwargs
    ) -> str:
        """
        Build Docker image from GCS source using Cloud Build
        """
        # Use strategy and builder patterns
        config = BuildConfig(image_name, self.project_id)
        config.dockerfile = dockerfile
        config.timeout_minutes = timeout_minutes
        config.machine_type = machine_type
        config.substitutions = substitutions
        config.tags = tags
        
        source_strategy = GcsSource(gcs_source)
        
        return self._execute_build(source_strategy, config)
    
    def build_from_github(
        self,
        repo_url: str,
        image_name: str,
        branch: Optional[str] = "main",
        commit_sha: Optional[str] = None,
        dockerfile: str = "Dockerfile",
        context_dir: str = ".",
        timeout_minutes: int = 10,
        machine_type: str = "E2_HIGHCPU_8",
        substitutions: Optional[Dict[str, str]] = None,
        tags: Optional[List[str]] = None,
        build_args: Optional[Dict[str, str]] = None,
        auto_generate_dockerfile: bool = False,
        dockerfile_type: str = "python",
    ) -> str:
        """
        Build Docker image from a GitHub repository using Cloud Build.
        
        Args:
            auto_generate_dockerfile: If True, will auto-generate Dockerfile
            dockerfile_type: Dockerfile type ("python", "nodejs", "generic", "simple")
        """
        if not repo_url:
            raise CloudBuildConfigError("repo_url is required")

        # Use strategy and builder patterns
        config = BuildConfig(image_name, self.project_id)
        config.dockerfile = dockerfile
        config.timeout_minutes = timeout_minutes
        config.machine_type = machine_type
        config.substitutions = substitutions
        config.tags = tags
        config.build_args = build_args
        
        source_strategy = GitHubSource(
            repo_url, 
            branch, 
            commit_sha, 
            context_dir,
            auto_generate_dockerfile,
            dockerfile_type
        )
        
        return self._execute_build(source_strategy, config)

    def get_build_status(self, build_id: str) -> cloudbuild_v1.Build:
        """
        Get build status by build ID
        
        Args:
            build_id: Cloud Build build ID
            
        Returns:
            Build: Build status information
        """
        request = cloudbuild_v1.GetBuildRequest(
            name=f"{self.parent_path}/builds/{build_id}"
        )
        return self.client.get_build(request=request)
    
    def list_builds(
        self,
        page_size: int = 20,
        filter_str: Optional[str] = None
    ) -> List[cloudbuild_v1.Build]:
        """
        List recent builds
        
        Args:
            page_size: Number of builds to return
            filter_str: Filter string for builds
            
        Returns:
            List[Build]: List of builds
        """
        request = cloudbuild_v1.ListBuildsRequest(
            parent=self.parent_path,
            page_size=page_size,
            filter=filter_str
        )
        
        builds = []
        for build in self.client.list_builds(request=request):
            builds.append(build)
            
        return builds
    
    def _execute_build(self, source_strategy: BuildSource, config: BuildConfig) -> str:
        """Execute build (template method)"""
        logger.info(f"Starting Cloud Build for image: {config.image_name}")
        logger.info(f"Project: {self.project_id}")
        
        try:
            # Create build configuration
            build_config = self._create_unified_build_config(source_strategy, config)
            
            # Create build request
            request = cloudbuild_v1.CreateBuildRequest(
                parent=self.parent_path,
                build=build_config
            )
            
            # Start build
            operation = self.client.create_build(request=request)
            
            # Wait for build to complete
            build_id = self._wait_for_build(operation)
            
            logger.info(f"Build completed successfully: {build_id}")
            return build_id
            
        except Exception as e:
            processed_error = error_processor.process_error(e)
            raise processed_error from e
    
    def _create_unified_build_config(self, source_strategy: BuildSource, config: BuildConfig) -> cloudbuild_v1.Build:
        """Unified build configuration creation method"""
        # Get source configuration
        source = source_strategy.create_source()
        
        # Get build steps
        steps = source_strategy.get_build_steps(config)
        
        # Set timeout
        timeout = duration_pb2.Duration()
        timeout.FromSeconds(config.timeout_minutes * 60)
        
        # Create build configuration
        build_config = cloudbuild_v1.Build(
            steps=steps,
            images=config.get_all_image_names(),
            timeout=timeout,
            options=cloudbuild_v1.BuildOptions(
                machine_type=self._normalize_machine_type(config.machine_type)
            )
        )
        
        # Add source (if available)
        if source:
            build_config.source = source
        
        # Add substitution variables
        if config.substitutions:
            build_config.substitutions = config.substitutions
        
        return build_config

    # Remove _create_github_build_config method, replaced by strategy pattern
    
    def _wait_for_build(
        self,
        operation,
        timeout_minutes: int = 30
    ) -> str:
        """
        Wait for build to complete
        
        Args:
            operation: Build operation
            timeout_minutes: Maximum wait time in minutes
            
        Returns:
            str: Build ID
            
        Raises:
            CloudBuildTimeoutError: If build times out
            CloudBuildError: If build fails
        """
        start_time = time.time()
        timeout_seconds = timeout_minutes * 60
        
        while True:
            if time.time() - start_time > timeout_seconds:
                raise CloudBuildTimeoutError(f"Build timed out after {timeout_minutes} minutes")
            
            # Check if operation is done
            if operation.done():
                break
                
            time.sleep(10)  # Wait 10 seconds before checking again
        
        # Get the result
        result = operation.result()
        
        if result.status != cloudbuild_v1.Build.Status.SUCCESS:
            error_msg = f"Build failed with status: {result.status}"
            if result.status_detail:
                error_msg += f" - {result.status_detail}"
            raise CloudBuildError(error_msg)
        
        return result.id 