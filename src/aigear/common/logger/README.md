# Aigear Logging System Quick Start Guide

## 📁 File Structure

```
src/aigear/common/logger/
├── __init__.py              # Module exports and interface
├── logger.py               # Traditional logging system (backward compatible)
├── stage_logger.py         # Stage-aware logging system
├── README.md    # This guide
├── README_zh.md              # Chinese documentation
├── test_simple.py         # Basic functionality tests
├── test_standalone.py     # Independent core functionality tests
├── test_new_logging.py    # Complete logging system tests
└── test_specific.py       # MLOps scenario tests
```

## 🚀 5-Minute Quick Setup

### 1. Install Dependencies

```bash
# Basic dependencies (required)
pip install psutil cloudpickle

# Enhanced features (optional)
pip install pydantic scikit-learn google-cloud-logging

# Or use automatic installation tool
python install_deps.py
```

### 2. Basic Usage

```python
from aigear.common.logger import create_stage_logger, PipelineStage

# Create training stage logger
training_logger = create_stage_logger(
    stage=PipelineStage.TRAINING,
    module_name=__name__
)

# Use context manager
with training_logger.stage_context() as logger:
    logger.info("Starting training")
    logger.log_epoch(1, 0.5, {"accuracy": 0.8})
```

### 3. Verify Installation

All test files are now located in the logger module for better organization:

```bash
# Navigate to logger module
cd src/aigear/common/logger

# Basic functionality test
python test_simple.py

# Complete functionality test
python test_standalone.py

# Specific scenario test
python test_specific.py

# New features test
python test_new_logging.py
```

## 📋 Detailed Usage Guide

### Creating Stage-Specific Loggers

```python
from aigear.common.logger import create_stage_logger, PipelineStage

# Training stage - detailed logs and GPU resources
training_logger = create_stage_logger(
    stage=PipelineStage.TRAINING,
    module_name="my_training_module",
    cpu_count=4,
    memory_limit="8GB",
    gpu_enabled=True,
    enable_cloud_logging=True
)

# Inference stage - optimized for latency, CPU-focused
inference_logger = create_stage_logger(
    stage=PipelineStage.INFERENCE,
    module_name="my_inference_module",
    cpu_count=2,
    memory_limit="4GB",
    gpu_enabled=False
)

# Deployment stage - infrastructure operations
deployment_logger = create_stage_logger(
    stage=PipelineStage.DEPLOYMENT,
    module_name="my_deployment_module",
    cpu_count=1,
    memory_limit="2GB"
)
```

### Using Specialized Methods

```python
# Training scenario
with training_logger.stage_context() as logger:
    # Basic logging
    logger.info("Starting model training")
    logger.debug("Loading dataset")

    # Training-specific methods
    for epoch in range(10):
        loss = train_one_epoch()
        metrics = evaluate_model()

        # Log training epoch
        logger.log_epoch(epoch, loss, metrics)

        # Save checkpoint
        if epoch % 5 == 0:
            logger.log_checkpoint(f"model_epoch_{epoch}.pth", epoch)

# Inference scenario
with inference_logger.stage_context() as logger:
    logger.info("Starting inference")

    start_time = time.time()
    predictions = model.predict(batch)
    latency = (time.time() - start_time) * 1000

    # Log inference results
    logger.log_prediction(
        latency_ms=latency,
        batch_size=len(batch),
        confidence=predictions.max()
    )

# Deployment scenario
with deployment_logger.stage_context() as logger:
    logger.info("Starting deployment process")

    # Create cloud resources
    logger.info("Creating Artifact Repository")
    create_artifact_repo()

    logger.info("Setting up load balancer")
    setup_load_balancer()

    logger.info("Deployment completed")
```

## 🔧 Configuration Options

### Stage Descriptions

| Stage | Default Log Level | Use Cases | Recommended Resources |
|-------|------------------|-----------|----------------------|
| TRAINING | DEBUG | Model training, hyperparameter tuning | GPU + high memory |
| INFERENCE | INFO | Online inference, batch prediction | CPU optimized + low latency |
| DEPLOYMENT | WARNING | Infrastructure deployment, operations | Basic configuration |
| PREPROCESSING | INFO | Data preprocessing, feature engineering | CPU + medium memory |
| EVALUATION | INFO | Model evaluation, A/B testing | CPU + medium memory |

### Basic Configuration

```python
logger = create_stage_logger(
    stage=PipelineStage.TRAINING,          # Required: MLOps stage
    module_name=__name__,                  # Required: module name
    cpu_count=2,                           # Optional: CPU cores (default: 1)
    memory_limit="4GB",                    # Optional: memory limit (default: "1GB")
    gpu_enabled=False,                     # Optional: enable GPU (default: False)
    enable_cloud_logging=False,            # Optional: enable cloud logging (default: False)
    project_id=None                        # Optional: GCP project ID (required for cloud logging)
)
```

## 📊 Log Output Formats

### Standard Log
```json
{
  "timestamp": "2025-09-30 11:43:14,413",
  "stage": "training",
  "level": "INFO",
  "message": "Starting training",
  "module": "my_module.training",
  "process_id": 28132,
  "thread_id": 6696
}
```

### Training-Specific Log
```json
{
  "timestamp": "2025-09-30 11:43:14,413",
  "stage": "training",
  "level": "INFO",
  "message": "Epoch 1 completed",
  "module": "my_module.training",
  "epoch": 1,
  "loss": 0.5,
  "metrics": {
    "accuracy": 0.85,
    "f1": 0.82
  }
}
```

### Inference-Specific Log
```json
{
  "timestamp": "2025-09-30 11:43:14,414",
  "stage": "inference",
  "level": "INFO",
  "message": "Prediction completed in 50.5ms",
  "module": "my_module.inference",
  "latency_ms": 50.5,
  "batch_size": 32,
  "prediction_confidence": 0.95
}
```

## 🧪 Testing

### Running Tests

All tests are now organized within the logger module:

```bash
# Navigate to the logger module
cd src/aigear/common/logger

# Run individual tests
python test_simple.py        # 4 basic tests
python test_standalone.py    # 4 core functionality tests
python test_specific.py      # 5 MLOps scenario tests
python test_new_logging.py   # 7 comprehensive tests

# Or from project root
cd src/aigear/common/logger && python test_simple.py
```

### Test Coverage

- **test_simple.py**: Basic import, logger creation, stage context, artifacts integration
- **test_standalone.py**: Direct imports, training scenarios, inference scenarios, deployment
- **test_specific.py**: Training pipelines, inference services, deployment operations, resource monitoring
- **test_new_logging.py**: Complete feature testing including error handling and compatibility

## 🔄 Backward Compatibility

```python
# Old code still works
from aigear.common.logger import Logging
old_logger = Logging(log_name="test").console_logging()
old_logger.info("Old method still works")

# Recommended new method
from aigear.common.logger import create_stage_logger, PipelineStage
new_logger = create_stage_logger(stage=PipelineStage.TRAINING, module_name=__name__)
with new_logger.stage_context() as log:
    log.info("Recommended to use new method")
```

## 🚨 Troubleshooting

### Common Issues

1. **Import Errors**
   ```bash
   # Check dependencies from project root
   python check_deps.py

   # Install missing dependencies
   pip install psutil cloudpickle pydantic
   ```

2. **Test Path Issues**
   ```bash
   # Make sure you're in the correct directory
   cd src/aigear/common/logger
   python test_simple.py
   ```

3. **Cloud Logging Connection Failed**
   - Will automatically fallback to console logging
   - Check GCP credentials and project ID configuration

4. **Windows Encoding Issues**
   ```bash
   # Redirect output to avoid console encoding issues
   python test_simple.py > output.txt 2>&1
   ```

## 💡 Best Practices

1. **Choose appropriate stage**: Select the corresponding PipelineStage based on actual use case
2. **Resource configuration**: Configure CPU, memory, GPU reasonably based on computational needs
3. **Log levels**: Use DEBUG for training, INFO for inference, WARNING+ for deployment
4. **Context management**: Always use `with logger.stage_context()` to ensure proper resource cleanup
5. **Specialized methods**: Use `log_epoch()`, `log_prediction()` and other specialized methods to record key metrics
6. **Modular testing**: Run tests from the logger module directory for organized development

## 🔗 Related Documentation

- [Chinese README](./README.md) - 中文说明文档
- [Project QUICK_START_GUIDE](../../../../QUICK_START_GUIDE.md) - 项目级快速开始指南
- [LOGGING_MIGRATION_GUIDE](../../../../LOGGING_MIGRATION_GUIDE.md) - 日志迁移指南
- [DEPENDENCY_RESOLUTION](../../../../DEPENDENCY_RESOLUTION.md) - 依赖解决方案

## 📈 What's New

### Logger Module Organization (Latest)
- ✅ **Modular Structure**: All logging components organized in `common/logger/`
- ✅ **Integrated Testing**: All test files moved to logger module
- ✅ **Unified Imports**: Single import path `from aigear.common.logger import ...`
- ✅ **Enhanced Documentation**: Both English and Chinese docs with API reference
- ✅ **Path Optimization**: Tests run directly from logger module directory