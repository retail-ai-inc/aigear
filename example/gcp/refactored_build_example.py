#!/usr/bin/env python3
"""
Refactored GCP Cloud Build Usage Example

This example demonstrates how to use the refactored GCP module for different types of builds:
1. Local source code build
2. GitHub repository build  
3. GCS source code build
4. Error handling demonstration

bash -c docker build -f Dockerfile -t asia-northeast1-docker.pkg.dev/ssc-ape-staging/medovik/ape3:latest .
push asia-northeast1-docker.pkg.dev/ssc-ape-staging/medovik/ape3:latest
"""

import sys
import os
import argparse
from pathlib import Path

# Add the src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))



from aigear.deploy.gcp.client import CloudBuildClientFactory
from aigear.deploy.gcp.builder import CloudBuildBuilder,BuildConfig, GitHubSource, LocalSource, GcsSource
from aigear.deploy.gcp.errors import (
    CloudBuildError, 
    CloudBuildConfigError, 
    CloudBuildAuthenticationError,
    error_processor
)
from aigear.common.logger import logger


def setup_environment():
    """Setup environment variables and validate configuration"""
    print("🔧 Setting up environment...")
    
    # Check required environment variables
    project_id = "ssc-ape-staging"
    if not project_id:
        print("❌ Please set GOOGLE_CLOUD_PROJECT environment variable")
        print("   export GOOGLE_CLOUD_PROJECT=your-project-id")
        return None
   
    # Set environment variables
    os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
    logger.info(f"Using GCP project: {project_id}")
    print(f"✅ Using Google Cloud project: {project_id}")
    return project_id


def example_github_build():
    """Example 1: Build image from GitHub repository"""
    print("\n🚀 Example 1: GitHub Repository Build")
    print("-" * 50)
    
    try:
        # Create builder
        builder = CloudBuildBuilder()
        
        # Use refactored strategy pattern
        build_id = builder.build_from_github(
            repo_url="https://github.com/GoogleCloudPlatform/cloud-builders.git",
            image_name="asia-northeast1-docker.pkg.dev/aigear-demo/aigear/test-app",  # Use Artifact Registry
            branch="master",
            dockerfile="docker/Dockerfile",
            context_dir="docker",
            timeout_minutes=15,
            machine_type="E2_HIGHCPU_8",
            tags=["latest", "github-build"],
            build_args={
                "BUILDPLATFORM": "linux/amd64",
                "BUILD_VERSION": "1.0.0"
            }
        )
        
        print(f"✅ GitHub build started successfully, Build ID: {build_id}")
        
        # Check build status
        build_status = builder.get_build_status(build_id)
        print(f"📊 Build status: {build_status.status}")
        
        return build_id
        
    except CloudBuildAuthenticationError as e:
        print(f"❌ Authentication error: {e}")
        print("Solutions:")
        print("1. Run: gcloud auth application-default login")
        print("2. Check IAM permissions")
    except CloudBuildConfigError as e:
        print(f"❌ Configuration error: {e}")
    except Exception as e:
        # Use new error processor
        processed_error = error_processor.process_error(e)
        print(f"❌ Build failed: {processed_error}")


def example_local_build():
    """Example 2: Build image from local source code"""
    print("\n🏠 Example 2: Local Source Code Build")
    print("-" * 50)
    
    try:
        # Prepare local source path
        source_path = Path(__file__).parent / "demo_app"
        
        # If example directory doesn't exist, create a simple demo
        if not source_path.exists():
            create_demo_app(source_path)
        
        builder = CloudBuildBuilder()
        
        build_id = builder.build_from_source(
            source_path=source_path,
            image_name="local-demo-app",
            dockerfile="Dockerfile",
            timeout_minutes=10,
            tags=["local-build", "demo"],
            substitutions={
                "_BUILD_ENV": "development",
                "_VERSION": "0.1.0"
            }
        )
        
        print(f"✅ Local build started successfully, Build ID: {build_id}")
        return build_id
        
    except Exception as e:
        processed_error = error_processor.process_error(e)
        print(f"❌ Local build failed: {processed_error}")
        if hasattr(processed_error, 'details'):
            print(f"Details: {processed_error.details}")


def example_gcs_build():
    """Example 3: Build image from GCS source code"""
    print("\n☁️ Example 3: GCS Source Code Build")
    print("-" * 50)
    
    try:
        builder = CloudBuildBuilder()
        
        # Assume source code is already uploaded to GCS
        gcs_source = "gs://your-build-bucket/source.tar.gz"
        
        build_id = builder.build_from_gcs(
            gcs_source=gcs_source,
            image_name="gcs-demo-app",
            dockerfile="Dockerfile",
            timeout_minutes=12,
            machine_type="E2_STANDARD_4",
            tags=["gcs-build"]
        )
        
        print(f"✅ GCS build started successfully, Build ID: {build_id}")
        return build_id
        
    except Exception as e:
        processed_error = error_processor.process_error(e)
        print(f"❌ GCS build failed: {processed_error}")


def example_advanced_config():
    """Example 4: Advanced configuration using BuildConfig class"""
    print("\n⚙️ Example 4: Advanced Configuration Usage")
    print("-" * 50)
    
    try:
        # Use BuildConfig builder pattern
        project_id = CloudBuildClientFactory.get_project_id()
        config = BuildConfig("advanced-app", project_id)
        config.dockerfile = "docker/Dockerfile"
        config.timeout_minutes = 20
        config.machine_type = "E2_HIGHCPU_32"
        config.tags = ["v1.0", "production", "advanced"]
        config.build_args = {
            "NODE_ENV": "production",
            "API_VERSION": "v2",
            "BUILDPLATFORM": "linux/amd64"
        }
        config.substitutions = {
            "_ENVIRONMENT": "prod",
            "_REGISTRY": "asia-northeast1-docker.pkg.dev"
        }
        
        # Use strategy pattern
        source_strategy = GitHubSource(
            repo_url="https://github.com/your-org/your-app.git",
            branch="main",
            context_dir="."
        )
        
        builder = CloudBuildBuilder()
        
        print("📋 Build Configuration:")
        print(f"   Image name: {config.get_full_image_name()}")
        print(f"   All tags: {config.get_all_image_names()}")
        print(f"   Build args: {config.build_args}")
        print(f"   Timeout: {config.timeout_minutes} minutes")
        
        # Execute build
        build_id = builder._execute_build(source_strategy, config)
        print(f"✅ Advanced configuration build started successfully, Build ID: {build_id}")
        
    except Exception as e:
        processed_error = error_processor.process_error(e)
        print(f"❌ Advanced configuration build failed: {processed_error}")


def example_error_handling():
    """Example 5: Error handling demonstration"""
    print("\n🚨 Example 5: Error Handling Demonstration")
    print("-" * 50)
    
    test_cases = [
        {
            "name": "Authentication Error",
            "action": lambda: CloudBuildClientFactory.get_client(project_id="invalid-project")
        },
        {
            "name": "Configuration Error", 
            "action": lambda: BuildConfig("", "").get_full_image_name()
        },
        {
            "name": "Resource Not Found Error",
            "action": lambda: CloudBuildBuilder().build_from_gcs(
                "gs://non-existent-bucket/source.tar.gz", 
                "test-image"
            )
        }
    ]
    
    for test_case in test_cases:
        print(f"\nTesting {test_case['name']}:")
        try:
            test_case['action']()
        except Exception as e:
            processed_error = error_processor.process_error(e)
            print(f"  Error type: {type(processed_error).__name__}")
            print(f"  Error code: {getattr(processed_error, 'error_code', 'N/A')}")
            print(f"  Error message: {str(processed_error)[:100]}...")


def list_recent_builds():
    """List recent builds"""
    print("\n📋 Recent Build List")
    print("-" * 50)
    
    try:
        builder = CloudBuildBuilder()
        builds = builder.list_builds(page_size=5)
        
        for i, build in enumerate(builds, 1):
            print(f"{i}. Build ID: {build.id}")
            print(f"   Status: {build.status}")
            print(f"   Created: {build.create_time}")
            if build.images:
                print(f"   Image: {build.images[0]}")
            print()
            
    except Exception as e:
        processed_error = error_processor.process_error(e)
        print(f"❌ Failed to get build list: {processed_error}")


def create_demo_app(demo_path: Path):
    """Create a simple demo application"""
    demo_path.mkdir(parents=True, exist_ok=True)
    
    # Create Dockerfile
    dockerfile_content = """FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py .

EXPOSE 8080

CMD ["python", "app.py"]
"""
    
    # Create requirements.txt
    requirements_content = """flask==2.3.2
gunicorn==21.2.0
"""
    
    # Create app.py
    app_content = """from flask import Flask

app = Flask(__name__)

@app.route('/')
def hello():
    return 'Hello from refactored Cloud Build!'

@app.route('/health')
def health():
    return {'status': 'healthy'}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
"""
    
    (demo_path / "Dockerfile").write_text(dockerfile_content)
    (demo_path / "requirements.txt").write_text(requirements_content)
    (demo_path / "app.py").write_text(app_content)
    
    print(f"✅ Created demo application at: {demo_path}")


def main():
    """Main function"""
    print("=" * 60)
    print("🏗️  Refactored GCP Cloud Build Usage Example")
    print("=" * 60)
    
    # Setup environment
    project_id = setup_environment()
    if not project_id:
        return
    
    # Clear client cache (for demonstration)
    print("\n🧹 Clearing client cache...")
    CloudBuildClientFactory.clear_cache()
    
    try:
        # Run various examples
        example_github_build()
        example_local_build() 
        example_gcs_build()
        example_advanced_config()
        example_error_handling()
        list_recent_builds()
        
        print("\n🎉 All examples completed!")
        print("\n💡 Tips:")
        print("1. Ensure Docker image registry is created (GCR or Artifact Registry)")
        print("2. Check IAM permissions: Cloud Build Editor, Storage Admin")
        print("3. Monitor build progress: https://console.cloud.google.com/cloud-build/builds")
        
    except KeyboardInterrupt:
        print("\n⏹️  User interrupted execution")
    except Exception as e:
        processed_error = error_processor.process_error(e)
        print(f"\n❌ Example execution failed: {processed_error}")


if __name__ == "__main__":
    main()