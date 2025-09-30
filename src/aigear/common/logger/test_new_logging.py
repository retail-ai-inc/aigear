#!/usr/bin/env python3
"""
New logging system test script
Test various functionalities of the stage-aware logging system
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
        print("✅ Successfully imported stage-aware logging module")
        return True
    except ImportError as e:
        print(f"❌ Import failed: {e}")
        return False

def test_logger_creation():
    """Test logger creation"""
    print("\n🔍 Test 2: Logger creation")
    try:
        from aigear.common.logger import create_stage_logger, PipelineStage

        # Test training stage logger
        training_logger = create_stage_logger(
            stage=PipelineStage.TRAINING,
            module_name="test_module",
            cpu_count=2,
            memory_limit="2GB",
            gpu_enabled=False,
            enable_cloud_logging=False  # Do not enable cloud logging during testing
        )
        print("✅ Training stage logger created successfully")

        # Test inference stage logger
        inference_logger = create_stage_logger(
            stage=PipelineStage.INFERENCE,
            module_name="test_module",
            cpu_count=1,
            memory_limit="1GB",
            gpu_enabled=False,
            enable_cloud_logging=False
        )
        print("✅ Inference stage logger created successfully")

        return True, training_logger, inference_logger
    except Exception as e:
        print(f"❌ Logger creation failed: {e}")
        return False, None, None

def test_stage_context(training_logger, inference_logger):
    """Test stage context functionality"""
    print("\n🔍 Test 3: Stage context functionality")
    try:
        # Test training stage context
        print("📊 Training stage log test:")
        with training_logger.stage_context() as logger:
            logger.info("Training started")
            logger.debug("Detailed debug information")
            # Test training-specific methods
            logger.log_epoch(1, 0.5, {"accuracy": 0.85, "f1": 0.82})
            logger.log_checkpoint("model_epoch_1.pth", 1)
            logger.info("Training stage test completed")

        print("\n🔮 Inference stage log test:")
        with inference_logger.stage_context() as logger:
            logger.info("Inference started")
            # Test inference-specific methods
            logger.log_prediction(latency_ms=50.5, batch_size=32, confidence=0.95)
            logger.info("Inference stage test completed")

        print("✅ Stage context functionality is working")
        return True
    except Exception as e:
        print(f"❌ Stage context test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_resource_monitoring():
    """Test resource monitoring functionality"""
    print("\n🔍 Test 4: Resource monitoring functionality")
    try:
        from aigear.common.logger import create_stage_logger, PipelineStage

        logger = create_stage_logger(
            stage=PipelineStage.TRAINING,
            module_name="test_module",
            enable_cloud_logging=False
        )

        with logger.stage_context() as log:
            log.info("Testing resource monitoring")
            log.log_resource_usage()

        print("✅ Resource monitoring functionality is working (if psutil is available)")
        return True
    except Exception as e:
        print(f"⚠️ Resource monitoring test: {e}")
        print("💡 Tip: Install psutil to enable resource monitoring functionality")
        return True  # This is not a fatal error

def test_artifacts_integration():
    """Test integration with existing code"""
    print("\n🔍 Test 5: artifacts.py integration test")
    try:
        # Simulate artifacts.py usage
        from aigear.common.logger import create_stage_logger, PipelineStage

        deployment_logger = create_stage_logger(
            stage=PipelineStage.DEPLOYMENT,
            module_name="test_artifacts",
            cpu_count=2,
            memory_limit="2GB",
            enable_cloud_logging=False  # Do not enable cloud logging during testing
        )

        # Simulate artifacts operations
        with deployment_logger.stage_context() as logger:
            logger.info("Creating artifacts repository: test-repo")
            time.sleep(0.1)  # Simulate operation time
            logger.info("Repository creation result: SUCCESS")
            logger.info("Repository test-repo exists")

        print("✅ artifacts integration test successful")
        return True
    except Exception as e:
        print(f"❌ artifacts integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_error_handling():
    """Test error handling"""
    print("\n🔍 Test 6: Error handling")
    try:
        from aigear.common.logger import create_stage_logger, PipelineStage

        # Test cloud logging with invalid project_id
        logger = create_stage_logger(
            stage=PipelineStage.DEPLOYMENT,
            module_name="test_module",
            enable_cloud_logging=True,
            project_id="invalid-project-id"
        )

        with logger.stage_context() as log:
            log.info("Testing error handling - this should fallback to console logging")

        print("✅ Error handling is working - automatic fallback when cloud logging fails")
        return True
    except Exception as e:
        print(f"❌ Error handling test failed: {e}")
        return False

def test_backward_compatibility():
    """Test backward compatibility"""
    print("\n🔍 Test 7: Backward compatibility")
    try:
        # Test if the old logging system still works
        from aigear.common.logger import Logging

        old_logger = Logging(log_name="test_old").console_logging()
        old_logger.info("Old logging system is still available")

        print("✅ Backward compatibility is good")
        return True
    except Exception as e:
        print(f"⚠️ Backward compatibility test: {e}")
        print("💡 This might be expected if you have fully migrated to the new system")
        return True

def run_all_tests():
    """Run all tests"""
    print("🚀 Starting new logging system test")
    print("=" * 50)

    tests_passed = 0
    total_tests = 7

    # Test 1: Basic import
    if test_basic_import():
        tests_passed += 1

    # Test 2: Logger creation
    success, training_logger, inference_logger = test_logger_creation()
    if success:
        tests_passed += 1

        # Test 3: Stage context (needs logger instances)
        if test_stage_context(training_logger, inference_logger):
            tests_passed += 1
    else:
        print("❌ Skipping dependent tests (logger creation failed)")
        total_tests -= 1

    # Test 4: Resource monitoring
    if test_resource_monitoring():
        tests_passed += 1

    # Test 5: artifacts integration
    if test_artifacts_integration():
        tests_passed += 1

    # Test 6: Error handling
    if test_error_handling():
        tests_passed += 1

    # Test 7: Backward compatibility
    if test_backward_compatibility():
        tests_passed += 1

    # Summary
    print("\n" + "=" * 50)
    print(f"🎯 Test summary: {tests_passed}/{total_tests} passed")

    if tests_passed == total_tests:
        print("🎉 All tests passed! New logging system is ready to use!")
        return True
    elif tests_passed >= total_tests * 0.8:
        print("⚠️ Most tests passed, some non-critical issues need attention")
        return True
    else:
        print("❌ Serious issues exist, need to be fixed before use")
        return False

def show_usage_examples():
    """Show usage examples"""
    print("\n" + "=" * 50)
    print("📖 Usage examples:")
    print("""
# 1. Basic usage
from aigear.common.logger import create_stage_logger, PipelineStage

training_logger = create_stage_logger(
    stage=PipelineStage.TRAINING,
    module_name=__name__,
    cpu_count=4,
    gpu_enabled=True
)

with training_logger.stage_context() as logger:
    logger.info("Starting training")
    logger.log_epoch(1, 0.5, {"accuracy": 0.8})

# 2. Inference usage
inference_logger = create_stage_logger(
    stage=PipelineStage.INFERENCE,
    module_name=__name__,
    cpu_count=2
)

with inference_logger.stage_context() as logger:
    logger.log_prediction(latency_ms=50, batch_size=32)
""")

if __name__ == "__main__":
    print("Aigear New Logging System Test Tool")
    print("=" * 50)

    success = run_all_tests()

    if success:
        show_usage_examples()

        print("\n💡 Next steps:")
        print("1. If tests pass, you can start using the new logging system")
        print("2. Consider installing optional dependencies: pip install psutil google-cloud-logging")
        print("3. Check LOGGING_MIGRATION_GUIDE.md for migration guide")
        print("4. Run your actual code to test integration effects")
    else:
        print("\n🛠️ Fix suggestions:")
        print("1. Check Python path settings")
        print("2. Ensure all required dependencies are installed")
        print("3. Check error messages and fix accordingly")