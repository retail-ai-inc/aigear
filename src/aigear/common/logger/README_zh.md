# Aigear 日志系统

## 📁 文件结构

```
src/aigear/common/logger/
├── __init__.py              # 模块导出和接口
├── logger.py               # 传统日志系统（向后兼容）
├── stage_logger.py         # Stage-aware 日志系统
├── README_zh.md            # 本文档（中文）
├── README.md              # 英文快速开始指南
├── test_simple.py         # 基础功能测试
├── test_standalone.py     # 独立核心功能测试
├── test_new_logging.py    # 完整日志系统测试
└── test_specific.py       # MLOps场景测试
```

## 🚀 5分钟快速开始

### 1. 安装依赖

```bash
# 基础依赖（必需）
pip install psutil cloudpickle

# 增强功能（可选）
pip install pydantic scikit-learn google-cloud-logging

# 或使用自动安装工具
python install_deps.py
```

### 2. 基本使用

```python
from aigear.common.logger import create_stage_logger, PipelineStage

# 创建训练阶段日志器
training_logger = create_stage_logger(
    stage=PipelineStage.TRAINING,
    module_name=__name__
)

# 使用上下文管理器
with training_logger.stage_context() as logger:
    logger.info("开始训练")
    logger.log_epoch(1, 0.5, {"accuracy": 0.8})
```

### 3. 验证安装

所有测试文件现在都位于logger模块中，便于组织管理：

```bash
# 进入logger模块目录
cd src/aigear/common/logger

# 基础功能测试
python test_simple.py

# 完整功能测试
python test_standalone.py

# 特定场景测试
python test_specific.py

# 新功能测试
python test_new_logging.py
```

## 📋 详细使用指南

### 创建不同阶段的日志器

```python
from aigear.common.logger import create_stage_logger, PipelineStage

# 训练阶段 - 需要详细日志和GPU资源
training_logger = create_stage_logger(
    stage=PipelineStage.TRAINING,
    module_name="my_training_module",
    cpu_count=4,
    memory_limit="8GB",
    gpu_enabled=True,
    enable_cloud_logging=True
)

# 推理阶段 - 优化延迟，CPU为主
inference_logger = create_stage_logger(
    stage=PipelineStage.INFERENCE,
    module_name="my_inference_module",
    cpu_count=2,
    memory_limit="4GB",
    gpu_enabled=False
)

# 部署阶段 - 基础设施操作
deployment_logger = create_stage_logger(
    stage=PipelineStage.DEPLOYMENT,
    module_name="my_deployment_module",
    cpu_count=1,
    memory_limit="2GB"
)
```

### 使用专用方法

```python
# 训练场景
with training_logger.stage_context() as logger:
    # 基础日志
    logger.info("开始训练模型")
    logger.debug("加载数据集")

    # 训练专用方法
    for epoch in range(10):
        loss = train_one_epoch()
        metrics = evaluate_model()

        # 记录训练轮次
        logger.log_epoch(epoch, loss, metrics)

        # 保存检查点
        if epoch % 5 == 0:
            logger.log_checkpoint(f"model_epoch_{epoch}.pth", epoch)

# 推理场景
with inference_logger.stage_context() as logger:
    logger.info("开始推理")

    start_time = time.time()
    predictions = model.predict(batch)
    latency = (time.time() - start_time) * 1000

    # 记录推理结果
    logger.log_prediction(
        latency_ms=latency,
        batch_size=len(batch),
        confidence=predictions.max()
    )

# 部署场景
with deployment_logger.stage_context() as logger:
    logger.info("开始部署流程")

    # 创建云资源
    logger.info("创建Artifact Repository")
    create_artifact_repo()

    logger.info("配置负载均衡器")
    setup_load_balancer()

    logger.info("部署完成")
```

## 🔧 配置选项

### 阶段说明

| 阶段 | 默认日志级别 | 适用场景 | 推荐资源配置 |
|------|-------------|----------|-------------|
| TRAINING | DEBUG | 模型训练、超参数调优 | GPU + 大内存 |
| INFERENCE | INFO | 在线推理、批量预测 | CPU优化 + 低延迟 |
| DEPLOYMENT | WARNING | 基础设施部署、运维 | 基础配置 |
| PREPROCESSING | INFO | 数据预处理、特征工程 | CPU + 中等内存 |
| EVALUATION | INFO | 模型评估、A/B测试 | CPU + 中等内存 |

### 基本配置

```python
logger = create_stage_logger(
    stage=PipelineStage.TRAINING,          # 必需：MLOps阶段
    module_name=__name__,                  # 必需：模块名称
    cpu_count=2,                           # 可选：CPU核心数 (默认: 1)
    memory_limit="4GB",                    # 可选：内存限制 (默认: "1GB")
    gpu_enabled=False,                     # 可选：是否启用GPU (默认: False)
    enable_cloud_logging=False,            # 可选：是否启用云日志 (默认: False)
    project_id=None                        # 可选：GCP项目ID (云日志需要)
)
```

## 📊 日志输出格式

### 标准日志
```json
{
  "timestamp": "2025-09-30 11:43:14,413",
  "stage": "training",
  "level": "INFO",
  "message": "开始训练",
  "module": "my_module.training",
  "process_id": 28132,
  "thread_id": 6696
}
```

### 训练专用日志
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

### 推理专用日志
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

## 🧪 测试

### 运行测试

所有测试现在都组织在logger模块内：

```bash
# 进入logger模块目录
cd src/aigear/common/logger

# 运行各项测试
python test_simple.py        # 4个基础测试
python test_standalone.py    # 4个核心功能测试
python test_specific.py      # 5个MLOps场景测试
python test_new_logging.py   # 7个综合功能测试

# 或从项目根目录运行
cd src/aigear/common/logger && python test_simple.py
```

### 测试覆盖

- **test_simple.py**: 基础导入、日志器创建、阶段上下文、artifacts集成
- **test_standalone.py**: 直接导入、训练场景、推理场景、部署管理
- **test_specific.py**: 训练管道、推理服务、部署操作、资源监控
- **test_new_logging.py**: 完整功能测试，包括错误处理和兼容性

## 🔄 向后兼容

```python
# 旧代码仍然可用
from aigear.common.logger import Logging
old_logger = Logging(log_name="test").console_logging()
old_logger.info("旧方式仍然有效")

# 推荐的新方式
from aigear.common.logger import create_stage_logger, PipelineStage
new_logger = create_stage_logger(stage=PipelineStage.TRAINING, module_name=__name__)
with new_logger.stage_context() as log:
    log.info("推荐使用新方式")
```

## 🚨 故障排除

### 常见问题

1. **导入错误**
   ```bash
   # 从项目根目录检查依赖
   python check_deps.py

   # 安装缺失依赖
   pip install psutil cloudpickle pydantic
   ```

2. **测试路径问题**
   ```bash
   # 确保在正确目录
   cd src/aigear/common/logger
   python test_simple.py
   ```

3. **云日志连接失败**
   - 会自动降级到控制台日志，不影响功能
   - 检查GCP凭据和项目ID配置

4. **Windows编码问题**
   ```bash
   # 重定向输出避免控制台编码问题
   python test_simple.py > output.txt 2>&1
   ```

## 💡 最佳实践

1. **选择合适的阶段**: 根据实际使用场景选择对应的PipelineStage
2. **资源配置**: 根据计算需求合理配置CPU、内存、GPU
3. **日志级别**: 训练用DEBUG，推理用INFO，部署用WARNING+
4. **上下文管理**: 始终使用`with logger.stage_context()`确保资源正确释放
5. **专用方法**: 使用`log_epoch()`, `log_prediction()`等专用方法记录关键指标
6. **模块化测试**: 在logger模块目录下运行测试，便于组织化开发

## 🔗 相关文档

- [英文快速开始指南](./QUICK_START_GUIDE.md) - English Quick Start Guide
- [项目级快速开始指南](../../../../QUICK_START_GUIDE.md) - 项目整体快速开始
- [日志迁移指南](../../../../LOGGING_MIGRATION_GUIDE.md) - 从旧系统迁移指南
- [依赖解决方案](../../../../DEPENDENCY_RESOLUTION.md) - 依赖问题解决方案

## 📈 最新更新

### Logger模块组织化 (最新)
- ✅ **模块化结构**: 所有日志组件统一组织在 `common/logger/` 下
- ✅ **集成测试**: 所有测试文件移至logger模块内
- ✅ **统一导入**: 单一导入路径 `from aigear.common.logger import ...`
- ✅ **增强文档**: 中英文文档齐全，包含完整API参考
- ✅ **路径优化**: 测试可直接在logger模块目录下运行

## 🔌 API 参考

### create_stage_logger()

创建stage-aware日志器。

**参数:**
- `stage` (PipelineStage): MLOps管道阶段
- `module_name` (str): 模块名称
- `cpu_count` (int, 可选): CPU核心数 (默认: 1)
- `memory_limit` (str, 可选): 内存限制 (默认: "1GB")
- `gpu_enabled` (bool, 可选): 启用GPU监控 (默认: False)
- `enable_cloud_logging` (bool, 可选): 启用云日志 (默认: False)
- `project_id` (str, 可选): GCP项目ID

**返回:**
- `StageAwareLogger`: 配置好的日志器实例

### PipelineStage

定义MLOps管道阶段的枚举：
- `TRAINING`: 模型训练和实验
- `INFERENCE`: 模型服务和预测
- `DEPLOYMENT`: 基础设施和部署操作
- `PREPROCESSING`: 数据预处理和特征工程
- `EVALUATION`: 模型评估和测试

### StageAwareLogger 方法

- `stage_context()`: 日志操作的上下文管理器
- `log_epoch(epoch, loss, metrics)`: 记录训练轮次信息
- `log_checkpoint(path, epoch)`: 记录模型检查点保存
- `log_prediction(latency_ms, batch_size, confidence)`: 记录推理结果
- `log_resource_usage()`: 记录当前资源使用情况

## 🔄 迁移指南

### 从旧系统迁移

**旧代码 (仍然支持):**
```python
from aigear.common.logger import Logging
logger = Logging(log_name="test").console_logging()
logger.info("旧方式")
```

**新代码 (推荐):**
```python
from aigear.common.logger import create_stage_logger, PipelineStage
logger = create_stage_logger(stage=PipelineStage.TRAINING, module_name=__name__)
with logger.stage_context() as log:
    log.info("新方式")
```

### 导入路径更新

所有日志相关的导入现在统一从 `aigear.common.logger` 开始：

```python
# ✅ 正确
from aigear.common.logger import create_stage_logger, PipelineStage

# ❌ 旧路径（已废弃）
from aigear.common.stage_logger import create_stage_logger, PipelineStage
```

## ⚠️ 注意事项

1. **Windows控制台编码**: 某些测试脚本在Windows控制台可能遇到Unicode编码问题，但不影响实际功能
2. **云日志配置**: 需要正确配置GCP凭据才能使用云日志功能
3. **资源监控**: 需要安装`psutil`包才能启用完整的资源监控功能
4. **测试组织**: 推荐在logger模块目录下运行测试以获得最佳开发体验