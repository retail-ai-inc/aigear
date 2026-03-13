# Configuration Parameter Guide


## 1. Basic Configuration (Basic)

### 1.1 Project Information

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `project_name` | `string` | Project name | `test_sklearn_pipeline` |
| `environment`  | `string` | Operating environment | `local` |

---

## 2. AIGear Configuration

### 2.1 GCP Cloud Services Configuration

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `gcp_project_id` | `string` | GCP Project ID | `xxx-ape-staging` |
| `location`       | `string` | GCP resource region | `asia-northeast1` |

#### 2.1.1 Storage Bucket Configuration (bucket)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Whether to enable storage bucket | `true` |
| `bucket_name` | `string` | Storage bucket name | `medovik-xxx-staging` |
| `bucket_name_for_release` | `string` | Release storage bucket name | `medovik-xxx-staging` |

#### 2.1.2 Cloud Build Configuration (cloud_build)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Whether to enable Cloud Build | `false` |
| `trigger_name` | `string` | Build trigger name | `test-pipeline-trigger` |
| `description` | `string` | Build trigger description | `Trigger for sklearn pipeline` |
| `repository` | `string` | Code repository address | `github.com/my-org/my-repo` |
| `repo_owner` | `string` | Repository owner | `my-org` |
| `repo_name` | `string` | Repository name | `my-repo` |
| `branch_pattern` | `string` | Branch matching pattern | `^main$` |
| `build_config` | `string` | Build configuration file | `cloudbuild.yaml` |
| `substitutions` | `string` | Build variable substitution | `_ENV=staging,_REGION=asia-northeast1` |

#### 2.1.3 Pre-configured VM Image Configuration (pre_vm_image)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Whether to enable pre-installed VM images | `false` |
| `machine_type` | `string` | Machine type | `n1-standard-4` |
| `gpu_type` | `string` | GPU type | `nvidia-tesla-t4` |
| `gpu_count` | `integer` | Number of GPUs | `1` |
| `boot_disk_gb` | `integer` | Boot disk size (GB) | `50` |
| `dlvm_family` | `string` | Deep Learning VM family | `tf2-latest-gpu` |
| `bake_vm` | `string` | VM baking configuration | `ml-image-bake-config` |
| `custom_image_name` | `string` | Custom image name | `my-custom-ml-image` |
| `bake_timeout_sec` | `integer` | Baking timeout (seconds) | `1200` |
| `bake_poll_interval_sec` | `integer` | Baking poll interval (seconds) | `20` |

#### 2.1.4 Cloud Function Configuration (cloud_function)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Whether to enable Cloud Functions | `true` |
| `function_name` | `string` | Function name | `test-pipeline-run` |

#### 2.1.5 IAM Permissions Configuration (iam)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Whether to enable IAM permissions configuration | `true` |
| `account_name` | `string` | Bound service account name | `test-pipeline-sa` |

#### 2.1.6 Message Queue Configuration (pub_sub)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Whether to enable Pub/Sub | `true` |
| `topic_name` | `string` | Topic name | `test-pipelines-pubsub` |

#### 2.1.7 Artifact Repository Configuration (artifacts)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Whether to enable Artifact Repository | `true` |
| `repository_name` | `string` | Repository name | `test-pipelines-image-whn` |

#### 2.1.8 Kubernetes Cluster Configuration (kubernetes)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Whether to enable Kubernetes | `true` |
| `cluster_name` | `string` | Cluster name | `my-grpc-cluster` |
| `num_nodes` | `integer` | Default number of nodes | `3` |
| `min_nodes` | `integer` | Autoscaling minimum node count | `1` |
| `max_nodes` | `integer` | Autoscaling maximum node count | `5` |

#### 2.1.9 Logging Configuration (logging)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `logging` | `boolean` | Whether to enable verbose logging | `false` |

---

## 3. Pipeline Task Configuration (Pipelines)

### 3.1 Pipeline Version Configuration (pipeline_version_1)

#### 3.1.1 Scheduler Configuration (scheduler)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `name` | `string` | Scheduled task name | `daily-sklearn-training` |
| `location` | `string` | Scheduler region | `asia-northeast1` |
| `schedule` | `string` | Scheduling rule (Cron expression) | `0 2 * * *` |
| `time_zone` | `string` | Time zone setting | `Asia/Tokyo` |

#### 3.1.2 Fetch Store List Configuration (fetch_store_list)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `parameters` | `object` | Additional parameter dictionary | `{"store_list_base_uri": "https://sandbox.raicart.io/..."}` |

##### 3.1.2.1 Task Resource Configuration (resources)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `vm_name` | `string` | Allocated Virtual Machine name | `fetch-store-task-vm` |
| `disk_size_gb` | `string` | Allocated disk size (GB) | `50` |
| `spec` | `string` | Computing resource specification | `e2-standard-4` |
| `on_host_maintenance` | `string` | Host maintenance policy | `MIGRATE` |

##### 3.1.2.2 Task Run Parameters (task_run_parameters)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `pipeline_version` | `string` | Specify the executed pipeline version | `pipeline_version_1` |
| `pipeline_step` | `string` | Specify the executed concrete pipeline step | `fetch_store_list` |

#### 3.1.3 Fetch Data Configuration (fetch_data)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `fetch_data` | `object` | Specific configuration for data fetching node | `{"mongo_uri_secret_name": "TRIAL_MONGO_URI"}` |

#### 3.1.4 Preprocessing Configuration (preprocessing)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `preprocessing` | `object` | Configuration for data cleaning and preprocessing node | `{"train_test_split": 0.8}` |

#### 3.1.5 Training Configuration (training)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `training` | `object` | Model training node configuration | `{"epochs": 35, "learning_rate": 0.1}` |

#### 3.1.6 Release Configuration (release)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `release_grpc` | `boolean` | Whether model deployment is released as a gRPC service | `false` |

##### 3.1.6.1 gRPC Detailed Configuration (grpc)

| Parameter | Type | Description | Example Value |
| :--- | :--- | :--- | :--- |
| `keep_alive.time` | `integer` | Keep alive heartbeat interval (seconds) | `60` |
| `keep_alive.timeout` | `integer` | Keep alive timeout (seconds) | `5` |
| `service_host` | `string` | Listening host address | `0.0.0.0` |
| `port` | `string` | Listening port | `50051` |
| `multi_processing.on` | `boolean` | Enable multi-processing mechanism | `false` |
| `multi_processing.process_count` | `integer` | Process count | `2` |
| `multi_processing.thread_count` | `integer` | Thread count | `10` |
| `model_path.model_file` | `string` | Mount/Load path of the trained model file | `/ape-model/${subsidiaryName}/ape4/LightSANs.pth` |
| `model_path.dataset_file` | `string` | Full path of the training set/dictionary associate data file | `/ape-model/${subsidiaryName}/ape4/SequentialDataset.pth` |
| `sentry.on` | `boolean` | Enable Sentry exception monitoring | `false` |
| `sentry.dsn` | `string` | Sentry DSN address | `https://examplePublicKey@o0.ingest.sentry.io/0` |
| `sentry.traces_sample_rate` | `float` | Sentry tracing sample rate | `1.0` |

---

## 4. Complete Configuration File Example (`env.json`)

Below is the latest `env.sample.json` example reference with suggested populated values:

```json
{
    "project_name": "test_sklearn_pipeline",
    "environment": "local",
    "aigear": {
        "gcp": {
            "gcp_project_id": "xxx-ape-staging",
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

## 5. Configuration Usage Instructions

> [!TIP]
> It is recommended to read the following steps to quickly use and apply configurations:

1. Guarantee that the configuration file is saved as `env.json` and placed securely in the project **root directory**.
2. Modify the corresponding parameter values according to your actual environment (e.g., Development/Test/Production) and requirements.
3. If a certain service is enabled (i.e., setting `on: true`), ensure that related permissions and dependencies are well-configured in the cloud.
4. For GCP-related configurations to take effect, ensure the local or CI/CD environment has the `gcloud` CLI tool installed and properly logged in.
5. Kubernetes-related configs require `kubectl` installed alongside access to the corresponding K8s cluster certificates and permissions.

---

## 6. Precautions & Best Practices

> [!WARNING]
> To ensure the secure and stable operation of the project, please strictly observe the following security conventions:

- **Sensitive Information Governance**: It is highly recommended NOT to **hardcode** sensitive information such as API Keys and Webhook URL callback addresses into the code repository. Inject them via GCP Secret Manager or environmental variables.
- **Environment Isolation**: The `env.json` files for the Production environment and the Development/Staging environment must be isolated from each other, strictly prohibiting cross-usage.
- **Configuration Backup**: Important environmental parameter files are recommended to be regularly backed up using secure tools or isolated branches in the code repository.
- **Update Activation**: After modifying the `env.json` file, it is generally required to **restart the application or container services** to ensure the latest configuration is fully loaded and applied.