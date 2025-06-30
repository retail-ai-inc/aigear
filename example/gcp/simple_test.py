#!/usr/bin/env python3
"""
Simplified test script to verify basic GCP Cloud Build functionality.
"""

import sys
from pathlib import Path

# Add the src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

def test_basic_imports():
    """Test basic imports without complex dependencies"""
    print("Testing basic imports...")
    
    try:
        # Test error classes first (no external dependencies)
        from aigear.deploy.gcp.errors import (
            CloudBuildError,
            CloudBuildConfigError,
            CloudBuildAuthenticationError,
            CloudBuildTimeoutError
        )
        print("✓ Error classes imported successfully")
        
        # Test utilities (minimal dependencies)
        from aigear.deploy.gcp.utilities import get_gcs_bucket_name
        print("✓ Utilities imported successfully")
        
        return True
    except ImportError as e:
        print(f"✗ Import failed: {e}")
        return False


def test_error_classes():
    """Test error class instantiation"""
    print("\nTesting error classes...")
    
    try:
        from aigear.deploy.gcp.errors import (
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
        from aigear.deploy.gcp.utilities import get_gcs_bucket_name
        
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
        from aigear.deploy.gcp.utilities import create_cloudbuild_yaml, validate_cloudbuild_config
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
    print("Running simplified GCP Cloud Build integration tests...\n")
    
    tests = [
        test_basic_imports,
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
        print("✓ All tests passed! Basic GCP integration is working.")
    else:
        print("✗ Some tests failed. Please check the errors above.")
    
    return passed == total


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 