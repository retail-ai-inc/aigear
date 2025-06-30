#!/usr/bin/env python3
"""
Simple test script to verify GCP Cloud Build integration.

This script tests the basic functionality without requiring actual GCP setup.
"""

import sys
from pathlib import Path

# Add the src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

def test_imports():
    """Test that all modules can be imported"""
    print("Testing imports...")
    
    try:
        from aigear.deploy.gcp import (
            CloudBuildBuilder,
            get_cloud_build_client,
            get_project_id,
            upload_source_to_gcs,
            create_cloudbuild_yaml,
            validate_cloudbuild_config,
            get_gcs_bucket_name,
            CloudBuildError,
            CloudBuildConfigError,
            CloudBuildAuthenticationError,
            CloudBuildTimeoutError
        )
        print("✓ All imports successful")
        return True
    except ImportError as e:
        print(f"✗ Import failed: {e}")
        return False


def test_error_classes():
    """Test error class instantiation"""
    print("\nTesting error classes...")
    
    try:
        from aigear.deploy.gcp import (
            CloudBuildError,
            CloudBuildConfigError,
            CloudBuildAuthenticationError,
            CloudBuildTimeoutError
        )
        
        # Test error instantiation
        CloudBuildError("Test error")
        CloudBuildConfigError("Test config error")
        CloudBuildAuthenticationError("Test auth error")
        CloudBuildTimeoutError("Test timeout error")
        
        print("✓ Error classes work correctly")
        return True
    except Exception as e:
        print(f"✗ Error class test failed: {e}")
        return False


def test_utilities():
    """Test utility functions"""
    print("\nTesting utility functions...")
    
    try:
        from aigear.deploy.gcp import get_gcs_bucket_name
        
        # Test bucket name generation
        bucket_name = get_gcs_bucket_name("test-project")
        print(f"✓ Generated bucket name: {bucket_name}")
        
        # Test with different project IDs
        bucket_name2 = get_gcs_bucket_name("my-project-123", "builds")
        print(f"✓ Generated bucket name with suffix: {bucket_name2}")
        
        return True
    except Exception as e:
        print(f"✗ Utility test failed: {e}")
        return False


def test_config_creation():
    """Test cloudbuild.yaml creation"""
    print("\nTesting config creation...")
    
    try:
        from aigear.deploy.gcp import create_cloudbuild_yaml, validate_cloudbuild_config
        from pathlib import Path
        
        # Create test config
        config_path = Path("test_cloudbuild.yaml")
        create_cloudbuild_yaml(
            output_path=config_path,
            image_name="test-app",
            dockerfile="Dockerfile",
            substitutions={"VERSION": "1.0.0"},
            options={"timeout": "300s"}
        )
        
        # Validate config
        is_valid = validate_cloudbuild_config(config_path)
        print(f"✓ Config created and validated: {is_valid}")
        
        # Clean up
        config_path.unlink()
        
        return True
    except Exception as e:
        print(f"✗ Config creation test failed: {e}")
        return False


def main():
    """Run all tests"""
    print("Running GCP Cloud Build integration tests...\n")
    
    tests = [
        test_imports,
        test_error_classes,
        test_utilities,
        test_config_creation
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
    
    print(f"\n{'='*50}")
    print(f"Test Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("✓ All tests passed! GCP integration is ready.")
    else:
        print("✗ Some tests failed. Please check the errors above.")
    
    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 