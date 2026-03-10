# AIGear 项目完全指南 (Full Guide)

> [!NOTE]
> 本文档是基于 AIGear 标准模板引擎 `aigear-init` 生成的机器学习项目（如 `my_service`）的**完全开发和部署指南**。
>
> 💡 若需快速查阅配置参数字典，请参考 [Configuration Guide](route-guide.md)。本文档着重于解释**项目架构、目录运行机制、部署原理以及核心 API 的使用**。

---

## 1. 核心架构设计

AIGear 旨在提供一套能够在本地开发并无缝接通 GCP (Google Cloud Platform) 或 Kubernetes (K8s) 环境的标准化 ML / 推荐系统微服务框架。

主要的设计原则包括：
- **配置与代码分离**：核心环境变量剥离至 `env.json` 或 Secret Manager。
- **环境隔离**：支持 `local`、`staging`、`production` 环境的无缝切换。
- **阶段解耦**：将标准的机器学习业务抽象为 `fetch_store_list`, `fetch_data`, `preprocessing`, `training`, `release` 等可扩展的 Node 管道节点。
- **极简部署**：支持将训练完成的模型直接通过 Cloud Build 封装发布为独立的 gRPC 微服务。

---

## 2. 目录结构详解

通过 `aigear-init` 生成的标准项目通常具备以下目录结构：

```text
my_service/
├── cloudbuild/           # GCP Cloud Build 持续集成配置文件夹
│   └── cloudbuild.yaml   # CI/CD 构建与发版控制脚本
├── docs/                 # 项目文档库 (包含 route-guide.md, full-guide.md)
├── kms/                  # (如有) 密钥管理或证书等敏感材料挂载点
├── src/                  # 核心源代码目录
│   ├── common/           # 跨 Pipeline 复用的通用功能模块 (配置读取、日志、通用工具)
│   └── pipelines/        # 核心业务逻辑实现层
│       └── pipeline_version_1/ # Pipeline 实例隔离层
│           ├── fetch_data/     # 数据获取逻辑
│           ├── preprocessing/  # 数据预处理与特征工程
│           ├── training/       # 模型训练主循环
│           └── release/        # 模型打包及 gRPC 节点相关处理
├── Dockerfile            # 用于容器化项目运行环境的构建规范
├── docker-compose.yml    # 用于本地快速拉起相关依赖(如数据库/缓存)的编排脚本
├── env.sample.json       # 环境配置参考文件 (本地运行需重命名为 env.json)
└── requirements.txt      # Python 核心依赖锁
```

### 2.1 结构工作流分析

- **Pipeline 层级划分**：`src/pipelines/` 下按 `pipeline_version_x` 划分版本，支持同一个代码仓库中运行多套算法实验流程。
- **单一职责流 (Data -> Feature -> Train -> Release)**：
  1. `fetch_data`: 负责对接 MongoDB / BigQuery，抽象 I/O 层。
  2. `preprocessing`: 负责特征打平聚合、异常剔除，输出标准 Numpy/Tensor。
  3. `training`: 纯粹的模型核心（包含早停策略、超参搜索等逻辑的挂载点）。
  4. `release`: 决定产出物去向，当 `env.json` 中配置了 `release_grpc: true` 时，该步骤可能会触发/编排针对模型制品的在线推理服务部署。

---

## 3. 高级部署原理

`route-guide.md` 表格中虽然枚举了所有配置，但它们在实际工程中的联动效果如下：

### 3.1 基于 GCP 构建流水线 (Cloud Build) 
当 `env.json` 设置了 `aigear.gcp.cloud_build.on = true`，且分支通过 `branch_pattern` (`^main$`) 匹配拦截推送时：
- GCP 触发器将读取源文件池。
- 执行 `cloudbuild.yaml`。利用 `substitutions` (`_ENV`, `_REGION` 等) 注入实际环境上下文。
- 执行测试并最终将 `Dockerfile` 打包到配置的 Artifacts 仓库 (`test-pipelines-image-whn`)。

### 3.2 Kubernetes gRPC 推理服务
如果业务场景需要实时在线评估，当配置了 `pipelines.xxx.release.grpc`：
- aigear 运行器或流水线将启用 gRPC 端点 (监听 `port: 50051`, `service_host: 0.0.0.0`)。
- 当 `multi_processing.on = true` 开启时，gRPC 服务器会以多进程模式运行 `process_count` 指定的 Worker 以榨干节点性能。
- 容器在 Kubernetes 集群 (`my-grpc-cluster`) 中被拉起拉去时，会加载预先配置的 `model_path` (通常对应 VPC/NAS 挂载点，或者刚通过 Bucket 下载后的 `LightSANs.pth` 制品)。
- 这套 Kubernetes 机制同时受到 `min_nodes/max_nodes` 参数的 HPA 自动伸缩影响。

### 3.3 监控与追踪 (Sentry & Logging)
- 打开 `sentry.on = true` 时，项目将读取 `dsn` 配置，所有未能被 `src` 层 catch 住的全局 Exception 和 gRPC 请求错误，都将自动上报给 Sentry，其中 `traces_sample_rate` 控制着追踪细节的精细度。
- 设置 `logging = true` 则强制启动 Aigear 控制的详尽 DEBUG 级别 GCP Cloud Logging 流。

---

## 4. Aigear 工具类库 (API 参考)

为了加快业务代码的书写，AIGear 框架为开发者内置了经过严格封装的云原生工具层（依赖 `aigear.*` 模块）。开发规范要求优先使用内置的 Client 访问基础平台，避免重复造轮子。

### 4.1 配置管理 (config.py)

**功能**：层级读取 `env.json`，完成对象化解析。

```python
from aigear.config import read_config

config = read_config()

# 对象化调用，避免硬编码的字典嵌套获取
project = config.gcp.gcp_project_id
is_grpc_enabled = config.pipelines.pipeline_version_1.release.release_grpc
```

### 4.2 云存储桶 (bucket.py)

**功能**：对 GCP Cloud Storage Bucket 进行极简的流转封装。

```python
from aigear.bucket import BucketClient

# 实例化桶通信协议
bucket_client = BucketClient(
    project_id="ssc-ape-staging",
    bucket_name="medovik-xxx-staging",
    bucket_on=True
)

# 直接操作
bucket_client.download(
    bucket_blob_name="models/LightSANs_v1.pth",
    local_blob_name="LightSANs.pth",
    local_blob_path="/tmp"
)

bucket_client.upload(
    local_blob_name="/tmp/metrics.csv",
    bucket_blob_name="outputs/metrics.csv"
)
```

### 4.3 数据库访问池化 (mongodb.py)

**功能**：管理 MongoDB 连接池，并安全集成 GCP 密码锁。

```python
from aigear.mongodb import MDBClient

mdb_client = MDBClient(project_id="ssc-ape-staging")

# [安全首选] 使用 GCP Secret Manager 取出 URI 字符串直接建立连接
db = mdb_client.connect_db_by_secret(
    mongo_uri_secret="TRIAL_MONGO_URI",
    db_name_secret="MONGO_DB_NAME"
)

# 执行标准的 pymongo 语法
collection = db["users"]
results = collection.find({"is_active": True})
```

### 4.4 扩展型云日志 (cloud_logging.py)

**功能**：平滑地兼容本地标准输出调试与线上结构化（JSON Payload）日志收集系统。

```python
from aigear.cloud_logging import Logging, wrap_logger, set_log_preprocessor

# 以双模或单模初始化日志对象
logger_controller = Logging(
    log_name="test_sklearn_pipeline_log",
    project_id="ssc-ape-staging"
)

# 开发环境只在控制台打印
logger = logger_controller.console_logging() 
# 线上环境推到 Cloud Logging 控制台归档
# logger = logger_controller.cloud_logging()

# 通过 wrap 给不同层加上模块前缀，利于追踪
train_logger = wrap_logger(logger, prefix="[TRAIN] ")
train_logger.info("Epoch 1/35 completed successfully.")
```

### 4.5 GCP 密钥管理 (secretmanager.py)

**功能**：剥离密码/Token 等高危字段。

```python
from aigear.secretmanager import SecretManager

secrets = SecretManager(project_id="ssc-ape-staging")

# 取出最新的私钥版本内容
client_secret = secrets.get_secret_val(
    secret_id="MANJU_CLIENT_SECRET",
    secret_version="latest"
)
```

### 4.6 第三方推送桥接 (slack.py)

**功能**：(若业务需要告警) 发送基于执行状态的运维通知。

```python
from aigear.slack import SlackNotifier

notifier = SlackNotifier(
    webhook_url="https://hooks.slack.com/services/YOUR/WEBHOOK",
    slack_on=True 
)

# 定制格式化发送事件报告
notifier.slack_webhook(
    message_content="Pipeline pipeline_version_1 fetch_data finished with 10k rows.",
    system_name="DataFetcher",
    pipeline_name="pipeline_version_1",
    step_name="fetch_data",
    event_type="success"  # success / warning / failure
)
```

### 4.7 全局任务调度器 (task_scheduler.py)

**功能**：引擎入口与指定 Node 的串联分发。这往往是整个 `Dockerfile` 或云函数的统一入口 (Entrypoint)。

```python
from aigear.task_scheduler import task_run

if __name__ == "__main__":
    # task_run() 会深度解析命令行参数 sys.argv，将对应 version 和 step 路由到对应的 src 模块并执行。
    task_run()
```

运行方式示例 (如果通过终端):
```bash
python main.py --version pipeline_version_1 --step pipeline_version_1.fetch_store_list
```
