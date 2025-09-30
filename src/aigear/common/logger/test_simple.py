#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simplified new logging system test script - Windows compatible version
"""
import sys
import os
import time
from pathlib import Path

# Add project path to Python path - updated for logger folder location
project_root = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(project_root / "src"))

def test_basic_import():
    """Test basic import functionality"""
    print("Test 1: Basic import functionality")
    try:
        from aigear.common.logger import create_stage_logger, PipelineStage
        print("Successfully imported stage-aware logging module")
        return True
    except ImportError as e:
        print(f"Import failed: {e}")
        return False

def test_logger_creation():
    """Test logger creation"""
    print("\nTest 2: Logger creation")
    try:
        from aigear.common.logger import create_stage_logger, PipelineStage

        # Test training stage logger
        training_logger = create_stage_logger(
            stage=PipelineStage.TRAINING,
            module_name="test_module",
            cpu_count=2,
            memory_limit="2GB",
            gpu_enabled=False,
            enable_cloud_logging=False
        )
        print("Training stage logger created successfully")

        # Test inference stage logger
        inference_logger = create_stage_logger(
            stage=PipelineStage.INFERENCE,
            module_name="test_module",
            cpu_count=1,
            memory_limit="1GB",
            gpu_enabled=False,
            enable_cloud_logging=False
        )
        print("Inference stage logger created successfully")

        return True, training_logger, inference_logger
    except Exception as e:
        print(f"Logger creation failed: {e}")
        return False, None, None

def test_stage_context(training_logger, inference_logger):
    """Test stage context functionality"""
    print("\nTest 3: Stage context functionality")
    try:
        # Test training stage context
        print("Training stage log test:")
        with training_logger.stage_context() as logger:
            logger.info("Training started")
            logger.debug("Detailed debug information")
            logger.log_epoch(1, 0.5, {"accuracy": 0.85, "f1": 0.82})
            logger.log_checkpoint("model_epoch_1.pth", 1)
            logger.info("Training stage test completed")

        print("\nInference stage log test:")
        with inference_logger.stage_context() as logger:
            logger.info("Inference started")
            logger.log_prediction(latency_ms=50.5, batch_size=32, confidence=0.95)
            logger.info("Inference stage test completed")

        print("Stage context functionality is working")
        return True
    except Exception as e:
        print(f"Stage context test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_artifacts_integration():
    """Test integration with existing code"""
    print("\nTest 4: artifacts.py integration test")
    try:
        from aigear.common.logger import create_stage_logger, PipelineStage

        deployment_logger = create_stage_logger(
            stage=PipelineStage.DEPLOYMENT,
            module_name="test_artifacts",
            cpu_count=2,
            memory_limit="2GB",
            enable_cloud_logging=False
        )

        # Simulate artifacts operations
        with deployment_logger.stage_context() as logger:
            logger.info("Creating artifacts repository: test-repo")
            time.sleep(0.1)
            logger.info("Repository creation result: SUCCESS")
            logger.info("Repository test-repo exists")

        print("artifacts integration test successful")
        return True
    except Exception as e:
        print(f"artifacts integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def run_all_tests():
    """Run all tests"""
    print("Starting new logging system test")
    print("=" * 50)

    tests_passed = 0
    total_tests = 4

    # Test 1: Basic import
    if test_basic_import():
        tests_passed += 1

    # Test 2: Logger creation
    success, training_logger, inference_logger = test_logger_creation()
    if success:
        tests_passed += 1

        # Test 3: Stage context
        if test_stage_context(training_logger, inference_logger):
            tests_passed += 1
    else:
        print("Skipping dependent tests (logger creation failed)")
        total_tests -= 1

    # Test 4: artifacts integration
    if test_artifacts_integration():
        tests_passed += 1

    # Summary
    print("\n" + "=" * 50)
    print(f"Test summary: {tests_passed}/{total_tests} passed")

    if tests_passed == total_tests:
        print("All tests passed! New logging system is ready to use!")
        return True
    elif tests_passed >= total_tests * 0.8:
        print("Most tests passed, some non-critical issues need attention")
        return True
    else:
        print("Serious issues exist, need to be fixed before use")
        return False

if __name__ == "__main__":
    print("Aigear New Logging System Test Tool")
    print("=" * 50)

    success = run_all_tests()

    if success:
        print("\nUsage example:")
        print("""
# Basic usage
from aigear.common.logger import create_stage_logger, PipelineStage

training_logger = create_stage_logger(
    stage=PipelineStage.TRAINING,
    module_name=__name__,
    enable_cloud_logging=False
)

with training_logger.stage_context() as logger:
    logger.info("Starting training")
    logger.log_epoch(1, 0.5, {"accuracy": 0.8})
""")

        print("\nNext steps:")
        print("1. Tests passed, can start using the new logging system")
        print("2. Recommended installation: pip install psutil")
        print("3. Check QUICK_START_GUIDE.md for detailed usage")
    else:
        print("\nFix suggestions:")
        print("1. Check Python path settings")
        print("2. Ensure all required dependencies are installed")
        print("3. Check error messages and fix accordingly")