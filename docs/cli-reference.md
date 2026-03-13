# CLI Reference

All CLI entry points are installed as standalone commands by `pip install aigear`.

| Command | Description |
|---|---|
| `aigear-init` | Initialize a new project scaffold |
| `aigear-gcp-infra` | Create GCP infrastructure (buckets, IAM, Pub/Sub, schedulers) |
| `aigear-workflow` | Run a single pipeline step locally |
| `aigear-scheduler` | Create a Cloud Scheduler job for pipeline steps |
| `aigear-image` | Build and optionally push Docker images to Artifact Registry |
| `aigear-grpc` | Start a gRPC model serving server |
| `aigear-deploy-model` | Deploy or delete a gRPC model service (local or GCP) |
| `aigear-env-schema` | Auto-generate a Pydantic schema from `env.json` |

---

### `aigear-init`

Initialize a project directory with templates for pipeline and model service containers.

```
aigear-init [--name NAME] [--pipeline_versions VERSIONS]
```

| Argument | Default | Description |
|---|---|---|
| `--name` | `template_project` | Project name (used as the directory name) |
| `--pipeline_versions` | `pipeline_version_1` | Comma-separated pipeline version names, e.g. `v1,v2` |

---

### `aigear-gcp-infra`

Read `env.json` and create all defined GCP resources (buckets, Pub/Sub topics, Cloud Scheduler jobs, service accounts, etc.).

```
aigear-gcp-infra
```

No arguments. Reads configuration from `env.json` in the current directory.

---

### `aigear-workflow`

Run a single named pipeline step locally by loading it as a Python module.

```
aigear-workflow --version VERSION --step STEP
```

| Argument | Description |
|---|---|
| `--version` | Pipeline version (e.g., `v1`) |
| `--step` | Dotted module path to the step function (e.g., `src.pipelines.v1.training.run`) |

---

### `aigear-scheduler`

Create a Cloud Scheduler job that triggers the given pipeline steps.

```
aigear-scheduler --version VERSION --step_names STEPS [image options]
```

| Argument | Default | Description |
|---|---|---|
| `--version` | `""` | Pipeline version |
| `--step_names` | `""` | Comma-separated step names, e.g. `fetch_data,training` |
| `--image_name` | `None` | Docker image name |
| `--image_version` | `latest` | Docker image version tag |
| `--force` | `false` | Force recreate image even if it already exists |
| `--push` | `false` | Push image to registry after build |

---

### `aigear-image`

Build Docker images for the pipeline (`Dockerfile.pl`) and/or model service (`Dockerfile.ms`), and optionally push to Artifact Registry.

```
aigear-image [--dockerfile_path PATH] [--build_context DIR]
             [--image_name NAME] [--image_version TAG]
             [--is_service] [--force] [--push]
```

| Argument | Default | Description |
|---|---|---|
| `--dockerfile_path` | `None` | Path to a specific Dockerfile. If omitted, builds both `Dockerfile.pl` and `Dockerfile.ms` |
| `--build_context` | `.` | Docker build context directory |
| `--image_name` | `None` | Override image name |
| `--image_version` | `latest` | Image version tag |
| `--is_service` | `false` | Mark image as a model service image (auto-set when building `Dockerfile.ms`) |
| `--force` | `false` | Rebuild even if the image already exists |
| `--push` | `false` | Push to Artifact Registry after build |

---

### `aigear-grpc`

Start a gRPC model serving server in the current process.

```
aigear-grpc --version VERSION --model_class_path CLASS_PATH
```

| Argument | Description |
|---|---|
| `--version` | Pipeline version |
| `--model_class_path` | Dotted path to the model class, e.g. `src.pipelines.v1.model_service.MyModel` |

---

### `aigear-deploy-model`

Deploy or delete a gRPC model service, either locally (via Docker Compose) or on GCP.

```
aigear-deploy-model --version VERSION --model_class_path CLASS_PATH
                    [--service_ports PORTS] [--replicas N] [--port PORT]
                    [--gcp] [--delete]
```

| Argument | Default | Description |
|---|---|---|
| `--version` | — | Pipeline version |
| `--model_class_path` | — | Dotted path to the model class |
| `--service_ports` | `50051` | Internal container port(s) |
| `--replicas` | `1` | Number of service replicas |
| `--port` | `50051` | External service port |
| `--gcp` | `false` | Deploy to GCP instead of locally |
| `--delete` | `false` | Delete the deployment instead of creating it |

---

### `aigear-env-schema`

Auto-generate a Pydantic schema file from the current `env.json`.

```
aigear-env-schema [--force]
```

| Argument | Description |
|---|---|
| `--force` | Regenerate the schema even if one already exists |
