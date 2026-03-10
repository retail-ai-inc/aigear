# 配置参数说明文档


## 1. 基本配置 (Basic)

### 1.1 项目信息

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `project_name` | `string` | 项目名称 | `test_sklearn_pipeline` |
| `environment`  | `string` | 运行环境 | `local` |

---

## 2. AIGear 配置

### 2.1 GCP 云服务配置

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `gcp_project_id` | `string` | GCP 项目 ID | `xxx-ape-staging` |
| `location`       | `string` | GCP 资源所在区域 | `asia-northeast1` |

#### 2.1.1 存储桶配置 (bucket)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | 是否启用存储桶 | `true` |
| `bucket_name` | `string` | 存储桶名称 | `medovik-xxx-staging` |
| `bucket_name_for_release` | `string` | 发布用存储桶名称 | `medovik-xxx-staging` |

#### 2.1.2 云构建配置 (cloud_build)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | 是否启用云构建 | `false` |
| `trigger_name` | `string` | 构建触发器名称 | `test-pipeline-trigger` |
| `description` | `string` | 构建触发器描述 | `Trigger for sklearn pipeline` |
| `repository` | `string` | 代码仓库地址 | `github.com/my-org/my-repo` |
| `repo_owner` | `string` | 仓库所有者 | `my-org` |
| `repo_name` | `string` | 仓库名称 | `my-repo` |
| `branch_pattern` | `string` | 分支匹配模式 | `^main$` |
| `build_config` | `string` | 构建配置文件 | `cloudbuild.yaml` |
| `substitutions` | `string` | 构建变量替换 | `_ENV=staging,_REGION=asia-northeast1` |

#### 2.1.3 预虚拟机镜像配置 (pre_vm_image)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | 是否启用预装虚拟机镜像 | `false` |
| `machine_type` | `string` | 机器类型 | `n1-standard-4` |
| `gpu_type` | `string` | GPU 类型 | `nvidia-tesla-t4` |
| `gpu_count` | `integer` | GPU 数量 | `1` |
| `boot_disk_gb` | `integer` | 启动磁盘大小 (GB) | `50` |
| `dlvm_family` | `string` | 深度学习虚拟机家族 | `tf2-latest-gpu` |
| `bake_vm` | `string` | 虚拟机烘焙配置 | `ml-image-bake-config` |
| `custom_image_name` | `string` | 自定义镜像名称 | `my-custom-ml-image` |
| `bake_timeout_sec` | `integer` | 烘焙超时时间 (秒) | `1200` |
| `bake_poll_interval_sec` | `integer` | 烘焙轮询间隔 (秒) | `20` |

#### 2.1.4 云函数配置 (cloud_function)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | 是否启用云函数 | `true` |
| `function_name` | `string` | 函数名称 | `test-pipeline-run` |

#### 2.1.5 IAM 权限配置 (iam)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | 是否启用 IAM 权限配置 | `true` |
| `account_name` | `string` | 绑定的服务账号名称 | `test-pipeline-sa` |

#### 2.1.6 消息队列配置 (pub_sub)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | 是否启用 Pub/Sub | `true` |
| `topic_name` | `string` | 主题名称 | `test-pipelines-pubsub` |

#### 2.1.7 工件仓库配置 (artifacts)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | 是否启用工件仓库 | `true` |
| `repository_name` | `string` | 仓库名称 | `test-pipelines-image-whn` |

#### 2.1.8 Kubernetes 集群配置 (kubernetes)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | 是否启用 Kubernetes | `true` |
| `cluster_name` | `string` | 集群名称 | `my-grpc-cluster` |
| `num_nodes` | `integer` | 默认节点数量 | `3` |
| `min_nodes` | `integer` | 自动伸缩最小节点数 | `1` |
| `max_nodes` | `integer` | 自动伸缩最大节点数 | `5` |

#### 2.1.9 日志配置 (logging)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `logging` | `boolean` | 是否启用详细日志记录 | `false` |

---

## 3. 管道任务配置 (Pipelines)

### 3.1 管道版本配置 (pipeline_version_1)

#### 3.1.1 调度器配置 (scheduler)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `name` | `string` | 调度任务名称 | `daily-sklearn-training` |
| `location` | `string` | 调度器所在地区 | `asia-northeast1` |
| `schedule` | `string` | 调度计划规则 (Cron 表达式) | `0 2 * * *` |
| `time_zone` | `string` | 时区设置 | `Asia/Tokyo` |

#### 3.1.2 商店列表获取配置 (fetch_store_list)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `parameters` | `object` | 额外参数字典 | `{"store_list_base_uri": "https://sandbox.raicart.io/..."}` |

##### 3.1.2.1 任务资源配置 (resources)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `vm_name` | `string` | 分配的虚拟机名称 | `fetch-store-task-vm` |
| `disk_size_gb` | `string` | 分配磁盘大小 (GB) | `50` |
| `spec` | `string` | 计算资源规格描述 | `e2-standard-4` |
| `on_host_maintenance` | `string` | 主机维护策略 (迁移等) | `MIGRATE` |

##### 3.1.2.2 任务运行参数 (task_run_parameters)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `pipeline_version` | `string` | 指定执行的管道版本 | `pipeline_version_1` |
| `pipeline_step` | `string` | 指定执行的具体管道步骤 | `fetch_store_list` |

#### 3.1.3 数据获取配置 (fetch_data)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `fetch_data` | `object` | 数据获取节点具体配置 | `{"mongo_uri_secret_name": "TRIAL_MONGO_URI"}` |

#### 3.1.4 预处理配置 (preprocessing)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `preprocessing` | `object` | 数据清洗和预处理节点配置 | `{"train_test_split": 0.8}` |

#### 3.1.5 训练配置 (training)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `training` | `object` | 模型训练节点配置 | `{"epochs": 35, "learning_rate": 0.1}` |

#### 3.1.6 发布配置 (release)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `release_grpc` | `boolean` | 模型部署是否发布为 gRPC 服务 | `false` |

##### 3.1.6.1 gRPC 详细配置 (grpc)

| 参数 | 类型 | 说明 | 示例值 |
| :--- | :--- | :--- | :--- |
| `keep_alive.time` | `integer` | 保持活动心跳间隔(秒) | `60` |
| `keep_alive.timeout` | `integer` | 保持活动超时时间(秒) | `5` |
| `service_host` | `string` | 监听的主机地址 | `0.0.0.0` |
| `port` | `string` | 监听端口 | `50051` |
| `multi_processing.on` | `boolean` | 启用多进程机制 | `false` |
| `multi_processing.process_count` | `integer` | 进程数量 | `2` |
| `multi_processing.thread_count` | `integer` | 线程数量 | `10` |
| `model_path.model_file` | `string` | 训练出的模型文件挂载/加载路径 | `/ape-model/${subsidiaryName}/ape4/LightSANs.pth` |
| `model_path.dataset_file` | `string` | 训练集/字典关联数据文件全路径 | `/ape-model/${subsidiaryName}/ape4/SequentialDataset.pth` |
| `sentry.on` | `boolean` | 启用 Sentry 异常监控 | `false` |
| `sentry.dsn` | `string` | Sentry DSN 地址 | `https://examplePublicKey@o0.ingest.sentry.io/0` |
| `sentry.traces_sample_rate` | `float` | Sentry 追踪采样率 | `1.0` |

---

## 4. 配置文件完整示例 (`env.json`)

以下是一个最新的、带建议填充值的 `env.sample.json` 示例参考：

```json
{
    "project_name": "test_sklearn_pipeline",
    "environment": "local",
    "aigear": {
        "gcp": {
            "gcp_project_id": "ssc-ape-staging",
            "location": "asia-northeast1",
            "bucket": {
                "on": true,
                "bucket_name": "medovik-xxx-staging",
                "bucket_name_for_release": "medovik-xxx-staging"
            },
            "cloud_build": {
                "on": false,
                "trigger_name": "test-pipeline-trigger",
                "description": "Trigger for sklearn pipeline",
                "repository": "github.com/my-org/my-repo",
                "repo_owner": "my-org",
                "repo_name": "my-repo",
                "branch_pattern": "^main$",
                "build_config": "cloudbuild.yaml",
                "substitutions": "_ENV=staging,_REGION=asia-northeast1"
            },
            "pre_vm_image": {
                "on": false,
                "machine_type": "n1-standard-4",
                "gpu_type": "nvidia-tesla-t4",
                "gpu_count": 1,
                "boot_disk_gb": 50,
                "dlvm_family": "tf2-latest-gpu",
                "bake_vm": "ml-image-bake-config",
                "custom_image_name": "my-custom-ml-image",
                "bake_timeout_sec": 1200,
                "bake_poll_interval_sec": 20
            },
            "cloud_function": {
                "on": true,
                "function_name": "test-pipeline-run"
            },
            "iam": {
                "on": true,
                "account_name": "test-pipeline-sa"
            },
            "pub_sub": {
                "on": true,
                "topic_name": "test-pipelines-pubsub"
            },
            "artifacts": {
                "on": true,
                "repository_name": "test-pipelines-image-whn"
            },
            "kubernetes": {
                "on": true,
                "cluster_name": "my-grpc-cluster",
                "num_nodes": 3,
                "min_nodes": 1,
                "max_nodes": 5
            },
            "logging": false
        }
    },
    "pipelines": {
        "pipeline_version_1": {
            "scheduler": {
                "name": "daily-sklearn-training",
                "location": "asia-northeast1",
                "schedule": "0 2 * * *",
                "time_zone": "Asia/Tokyo"
            },
            "fetch_store_list": {
                "parameters": {
                    "store_list_base_uri": "https://sandbox.raicart.io/..."
                },
                "resources": {
                    "vm_name": "fetch-store-task-vm",
                    "disk_size_gb": "50",
                    "spec": "e2-standard-4",
                    "on_host_maintenance": "MIGRATE"
                },
                "task_run_parameters": {
                    "pipeline_version": "pipeline_version_1",
                    "pipeline_step": "fetch_store_list"
                }
            },
            "fetch_data": {
                "mongo_uri_secret_name": "TRIAL_MONGO_URI"
            },
            "preprocessing": {
                 "train_test_split": 0.8
            },
            "training": {
                 "epochs": 35,
                 "learning_rate": 0.1
            },
            "release": {
                "release_grpc": false,
                "grpc": {
                    "keep_alive": {
                        "time": 60,
                        "timeout": 5
                    },
                    "service_host": "0.0.0.0",
                    "port": "50051",
                    "multi_processing": {
                        "on": false,
                        "process_count": 2,
                        "thread_count": 10
                    },
                    "model_path": {
                        "model_file": "/ape-model/${subsidiaryName}/ape4/LightSANs.pth",
                        "dataset_file": "/ape-model/${subsidiaryName}/ape4/SequentialDataset.pth"
                    },
                    "sentry": {
                        "on": false,
                        "dsn": "https://examplePublicKey@o0.ingest.sentry.io/0",
                        "traces_sample_rate": 1.0
                    }
                }
            }
        }
    }
}
```

---

## 5. 配置使用说明

> [!TIP]
> 推荐阅读以下步骤来快速使用并应用配置：

1. 确保将配置文件保存为 `env.json`，并放置在项目 **根目录**。
2. 根据你所处的实际环境（如开发/测试/生产）和需求修改对应的参数值。
3. 如果将某项服务启用（即设置了 `on: true`），请务必确保已在云端配置好相关的权限与依赖项。
4. GCP 相关的配置生效，需要确保本地或 CI/CD 环境已安装并正确登录了 `gcloud` CLI 工具。
5. Kubernetes 相关配置要求已安装 `kubectl`，并且拥有访问对应 K8s 集群的证书与权限。

---

## 6. 注意事项 \& 最佳实践

> [!WARNING]
> 为了保障项目的安全与稳定运行，请务必遵守以下安全约定：

- **敏感信息治理**：强烈建议不要将 API 密钥（API Keys）、Webhook URL 回调地址等敏感信息 **硬编码** 并提交到代码仓库中。通过 GCP Secret Manager 管理器或环境变量注入。
- **环境隔离**：生产环境（Production）与开发/测试环境（Development / Staging）的 `env.json` 必须相互隔离，严禁混用。
- **配置备份**：重要的环境参数文件建议使用代码仓库隔离分支或安全工具定期备份。
- **更新生效**：修改 `env.json` 文件后，通常需要**重启应用程序或容器服务**，以确保最新的配置被完整加载和应用。