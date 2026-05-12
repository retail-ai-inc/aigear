# Configuration Parameter Guide

## 1. Basic Configuration

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `project_name` | `string` | Project name | `aigear_sklearn_pipeline` |
| `environment` | `string` | Operating environment | `local` |

---

## 2. AIGear Configuration (`aigear`)

### 2.1 GCP Configuration (`aigear.gcp`)

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `gcp_project_id` | `string` | GCP Project ID | `my-project-staging` |
| `location` | `string` | Default GCP resource region | `asia-northeast1` |

#### 2.1.1 Storage Bucket (`bucket`)

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Enable GCS bucket creation | `true` |
| `bucket_name` | `string` | Bucket used by pipeline jobs | `test-sklearn-pipeline` |
| `bucket_name_for_release` | `string` | Bucket used by model serving | `test-sklearn-pipeline-service` |

#### 2.1.2 Cloud Build (`cloud_build`)

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Enable Cloud Build trigger creation | `false` |
| `trigger_name` | `string` | Trigger name | `my-pipeline-trigger` |
| `description` | `string` | Trigger description | `Trigger for sklearn pipeline` |
| `repo_owner` | `string` | Repository owner | `my-org` |
| `repo_name` | `string` | Repository name | `my-repo` |
| `event` | `string` | Trigger event type (`push` or `tag`) | `push` |
| `branch_pattern` | `string` | Branch name or regex pattern (used when `event` is `push`) | `^main$` |
| `tag_pattern` | `string` | Tag pattern (used when `event` is `tag`) | `^v.*$` |
> The Cloud Build config file path is fixed at `/cloudbuild/cloudbuild.yaml` and cannot be changed. `aigear-init` generates this file automatically.

#### 2.1.3 Cloud KMS (`kms`)

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Enable KMS keyring and key creation | `true` |
| `keyring_name` | `string` | KMS keyring name | `my-pipeline-keyring` |
| `key_name` | `string` | KMS encryption key name | `my-pipeline-key` |

> Used to encrypt and decrypt `env.json`. The pre-commit hook installed by `aigear-init` enforces that `env.json` is encrypted before committing.

#### 2.1.4 Pre-built VM Image (`pre_vm_image`)

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Enable pre-baked VM image | `false` |

> More creation parameters, use `aigear.infrastructure.gcp.pre_vm_image.PreVMImage`.

#### 2.1.5 Cloud Function (`cloud_function`)

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Enable Cloud Function creation | `true` |
| `function_name` | `string` | Function name | `test-sklearn-pipeline-run` |

#### 2.1.6 IAM (`iam`)

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Enable service account creation | `true` |
| `account_name` | `string` | Service account name | `test-sklearn-pipeline` |

#### 2.1.7 Pub/Sub (`pub_sub`)

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Enable Pub/Sub topic creation | `true` |
| `topic_name` | `string` | Topic name | `test-sklearn-pipeline-pubsub` |

#### 2.1.8 Artifact Registry (`artifacts`)

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Enable Artifact Registry creation | `true` |
| `repository_name` | `string` | Repository name | `test-sklearn-pipeline-images` |
| `ms_image_name` | `string` | Model service Docker image name | `my-model-service` |
| `pl_image_name` | `string` | Pipeline Docker image name | `my-pipeline` |
| `image_tag` | `string` | Image tag | `latest` |

#### 2.1.9 Kubernetes (`kubernetes`)

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Enable GKE cluster creation | `true` |
| `cluster_name` | `string` | Cluster name | `test-sklearn-pipeline-service-cluster` |
| `num_nodes` | `integer` | Default node count | `1` |
| `min_nodes` | `integer` | Autoscaling minimum | `1` |
| `max_nodes` | `integer` | Autoscaling maximum | `5` |

#### 2.1.9 Logging

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `logging` | `boolean` | Enable verbose GCP logging | `false` |

---

### 2.2 Slack Configuration (`aigear.slack`)

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `on` | `boolean` | Enable Slack notifications | `false` |
| `webhook_url` | `string` | Slack incoming webhook URL | `https://hooks.slack.com/...` |

---

## 3. Pipeline Configuration (`pipelines`)

Pipelines are keyed by version name (e.g., `logistic_regression`, `v1`). Each version contains the following top-level fields:

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `venv_pl` | `string` | *(optional)* Venv name for pipeline training steps. Resolves to `/opt/venv/<name>/bin/aigear-task`. Omit to use the image's default `aigear-task`. | `"pl"` |

### 3.1 Scheduler (`scheduler`)

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `name` | `string` | Cloud Scheduler job name | `test-sklearn-pipeline` |
| `schedule` | `string` | Cron expression — [reference](https://cloud.google.com/scheduler/docs/configuring/cron-job-schedules) | `45 21 * * 0` |
| `time_zone` | `string` | Scheduler time zone (IANA format) — [reference](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) | `Asia/Tokyo` |

### 3.2 Pipeline Steps

Each pipeline step (e.g., `fetch_data`, `preprocessing`, `training`) shares the same structure:

| Field | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `parameters` | `object` | Step-specific input parameters | `{"data_file_name": "breast_cancer.pkl"}` |
| `resources.vm_name` | `string` | Ephemeral VM name for this step | `test-sklearn-fetch-data-vm` |
| `resources.disk_size_gb` | `string` | Boot disk size (GB) | `"50"` |
| `resources.spec` | `string` | Machine type — [reference](https://docs.cloud.google.com/compute/docs/general-purpose-machines#e2-shared-core) | `e2-medium` |
| `resources.gpu` | `boolean` | Whether to attach a GPU | `false` |
| `pipeline_step` | `string` | Python dotted path to the step function | `src.pipelines.logistic_regression.fetch_data.data_from_sklearn.fetch_data` |

### 3.3 Model Service (`model_service`)

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `release` | `boolean` | Deploy as a gRPC service after training | `true` |
| `venv_ms` | `string` | *(optional)* Venv name for the model service container. Resolves to `/opt/venv/<name>/bin/aigear-task`. Omit to use the image's default `aigear-task`. | `"ms"` |
| `model_class_path` | `string` | Python dotted path to the ModelService class | `src.pipelines.logistic_regression.model_service.logistic_regression_service.ModelService` |
| `resources.vm_name` | `string` | VM name for deploy command | `test-sklearn-model-service-vm` |
| `resources.disk_size_gb` | `string` | Disk size (GB) | `"50"` |
| `resources.spec` | `string` | Machine type — [reference](https://docs.cloud.google.com/compute/docs/general-purpose-machines#e2-shared-core) | `e2-medium` |
| `resources.gpu` | `boolean` | Whether to attach a GPU | `false` |

#### 3.3.1 gRPC Configuration (`model_service.grpc`)

| Parameter | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `keep_alive.time` | `integer` | Keepalive ping interval (seconds) | `60` |
| `keep_alive.timeout` | `integer` | Keepalive timeout (seconds) | `5` |
| `service_host` | `string` | Listening host | `0.0.0.0` |
| `port` | `string` | Listening port | `50051` |
| `multi_processing.on` | `boolean` | Enable multi-processing | `false` |
| `multi_processing.process_count` | `integer` | Number of processes | `2` |
| `multi_processing.thread_count` | `integer` | Threads per process | `10` |
| `multi_processing.disable_omp` | `boolean` | Disable OpenMP/framework-level thread parallelism | `false` |

> **`disable_omp`**: Controls whether OpenMP and framework-level thread parallelism (used internally by NumPy, scikit-learn, PyTorch, etc.) is disabled across worker processes.
>
> Set to `true` **only** when all of the following apply:
> - Inference is **online / single-request** (not batch) — batch inference benefits from internal parallelism to process multiple samples simultaneously
> - There are **no significant I/O operations** — I/O naturally yields the CPU, so internal threads do not compete
>
> When both conditions hold, each worker process spawns as many OMP threads as there are CPU cores (the default). With multiple processes running, the total number of threads far exceeds the available cores, causing the OS scheduler to context-switch excessively and reducing API throughput. Setting `disable_omp: true` caps each process to a single internal thread, so threads across processes no longer compete for cores.
| `sentry.on` | `boolean` | Enable Sentry error monitoring | `false` |
| `sentry.dsn` | `string` | Sentry DSN | `https://...@o0.ingest.sentry.io/0` |
| `sentry.traces_sample_rate` | `float` | Sentry trace sample rate | `1.0` |

---

## 4. Complete `env.json` Example

```json
{
    "project_name": "aigear_sklearn_pipeline",
    "environment": "staging",
    "aigear": {
        "gcp": {
            "gcp_project_id": "",
            "location": "asia-northeast1",
            "bucket": {
                "on": true,
                "bucket_name": "test-sklearn-pipeline",
                "bucket_name_for_release": "test-sklearn-pipeline-service"
            },
            "cloud_build": {
                "on": false,
                "trigger_name": "",
                "description": "",
                "repo_owner": "***",
                "repo_name": "",
                "event": "push",
                "branch_pattern": "",
                "tag_pattern": ""
            },
            "kms": {
                "on": true,
                "keyring_name": "my-pipeline-keyring",
                "key_name": "my-pipeline-key"
            },
            "pre_vm_image": {
                "on": false
            },
            "cloud_function": {
                "on": true,
                "function_name": "test-sklearn-pipeline-run"
            },
            "iam": {
                "on": true,
                "account_name": "test-sklearn-pipeline"
            },
            "pub_sub": {
                "on": true,
                "topic_name": "test-sklearn-pipeline-pubsub"
            },
            "artifacts": {
                "on": true,
                "repository_name": "test-sklearn-pipeline-images",
                "ms_image_name": "test-sklearn-model-service",
                "pl_image_name": "test-sklearn",
                "image_tag": "latest"
            },
            "kubernetes": {
                "on": true,
                "cluster_name": "test-sklearn-pipeline-service-cluster",
                "num_nodes": 1,
                "min_nodes": 1,
                "max_nodes": 5
            },
            "logging": false
        },
        "slack": {
            "on": false,
            "webhook_url": ""
        }
    },
    "pipelines": {
        "logistic_regression": {
            "venv_pl": "pl",
            "scheduler": {
                "name": "test-sklearn-pipeline",
                "schedule": "45 21 * * 0",
                "time_zone": "Asia/Tokyo"
            },
            "fetch_data": {
                "parameters": {
                    "data_file_name": "breast_cancer.pkl"
                },
                "resources": {
                    "vm_name": "test-sklearn-fetch-data-vm",
                    "disk_size_gb": "50",
                    "spec": "e2-medium",
                    "gpu": false
                },
                "pipeline_step": "src.pipelines.logistic_regression.fetch_data.data_from_sklearn.fetch_data"
            },
            "preprocessing": {
                "parameters": {
                    "feature_file_name": "features_data.pkl",
                    "scaler_model": "scaler_model.pkl"
                },
                "resources": {
                    "vm_name": "test-sklearn-preprocessing-vm",
                    "disk_size_gb": "50",
                    "spec": "e2-medium",
                    "gpu": false
                },
                "pipeline_step": "src.pipelines.logistic_regression.preprocessing.feature_processing.feature_processing"
            },
            "training": {
                "parameters": {
                    "logistic_model": "logistic_regression.pkl"
                },
                "resources": {
                    "vm_name": "test-sklearn-training-vm",
                    "disk_size_gb": "50",
                    "spec": "e2-medium",
                    "gpu": false
                },
                "pipeline_step": "src.pipelines.logistic_regression.training.train.train_model"
            },
            "model_service": {
                "release": true,
                "venv_ms": "ms",
                "grpc": {
                    "keep_alive": {
                        "time": 60,
                        "timeout": 5
                    },
                    "service_host": "0.0.0.0",
                    "port": "50051",
                    "multi_processing": {
                        "on": false,
                        "process_count": 3,
                        "thread_count": 1,
                        "disable_omp": true
                    },
                    "sentry": {
                        "on": false,
                        "dsn": "",
                        "traces_sample_rate": 1.0
                    }
                },
                "resources": {
                    "vm_name": "test-sklearn-model-service-vm",
                    "disk_size_gb": "50",
                    "spec": "e2-medium",
                    "gpu": false
                },
                "model_class_path": "src.pipelines.logistic_regression.model_service.logistic_regression_service.ModelService"
            }
        }
    }
}
```

---

## 5. CLI Command Reference

All CLI commands read `env.json` from the current working directory.

| Command | Arguments | Description |
| :--- | :--- | :--- |
| `aigear-init` | `--name`, `--pipeline_versions` | Scaffold a new project |
| `aigear-gcp-infra` | `--create`, `--update`, `--delete`, `--status` | Provision, update, tear down, or query all GCP infrastructure defined in `env.json` |
| `aigear-env-schema` | `--generate`, `--force` | Auto-generate a Pydantic schema from `env.json` |
| `aigear-kms-env` | `--encrypt`, `--decrypt`, `--environment`, `--input`, `--output`, `--project-id`, `--location`, `--keyring`, `--key` | Encrypt or decrypt `env.json` using Cloud KMS |
| `aigear-image` | `--create`, `--delete`, `--retag`, `--push`, `--all`, `--dockerfile_path`, `--build_context`, `--is_service`, `--src_tag`, `--target_tag` | Build, delete, or re-tag Docker images; optionally sync to Artifact Registry |
| `aigear-scheduler` | `--create`, `--update`, `--delete`, `--status`, `--list`, `--run`, `--pause`, `--resume`, `--version`, `--step_names` | Manage Cloud Scheduler jobs for pipeline steps |
| `aigear-task workflow` | `--version`, `--step` | Run a single pipeline step locally (step name looked up from `env.json`) |
| `aigear-task grpc` | `--version` | Start a gRPC model serving server (model class path read from `env.json`) |
| `aigear-model` | `--version`, `--local`/`--staging`/`--production`, `--yaml`/`--deploy`/`--update`/`--delete`/`--status`, `--service_ports`, `--replicas`, `--port` | Generate YAML and manage the full lifecycle of a gRPC model service (local Kubernetes or GCP) |

---

## 6. Usage Instructions

> [!TIP]
> Follow these steps to get started quickly:

1. Save the configuration as `env.json` in the project **root directory**.
2. Fill in `gcp_project_id` and other environment-specific values.
3. Set `on: true` only for services you intend to use — unused services should remain `false`.
4. Run `aigear-gcp-infra --create` to provision infrastructure (requires GCP owner-level permissions; recommended to run in Cloud Shell).
5. Run pipeline steps with `aigear-task workflow --version <version> --step <step_name>`.

---

## 7. Security Best Practices

> [!WARNING]
> Observe the following conventions to keep the project secure:

- **`env.json` encryption**: Encrypt `env.json` with Cloud KMS using `aigear-kms-env --encrypt` before committing. `aigear-init` automatically installs a git pre-commit hook that blocks commits if `env.json` is newer than its encrypted counterpart — ensuring the plaintext file is never accidentally pushed. To decrypt on a new machine: `aigear-kms-env --decrypt --project-id ID --location LOC --keyring NAME --key NAME`.
- **Permission separation**: Infrastructure creation (`aigear-gcp-infra`) requires owner-level GCP permissions and should be run in Cloud Shell. Day-to-day pipeline commands require only developer-level permissions.
- **Environment isolation**: Keep separate `env.json` files for production and staging; never share them across environments.
- **Restart after changes**: After modifying `env.json`, restart the application or container to load the updated configuration.
