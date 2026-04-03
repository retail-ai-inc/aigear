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

> **Git pre-commit hook**: `aigear-init` automatically installs a pre-commit hook in the new project's `.git/hooks/` directory. The hook blocks any commit where `env.json` is newer than its encrypted counterpart, preventing accidental commits of plaintext configuration.

---

### `aigear-gcp-infra`

Read `env.json` and create all defined GCP resources (buckets, Pub/Sub topics, Cloud Function, Artifact Registry, KMS, GKE cluster, service accounts, etc.).

```
aigear-gcp-infra [--create]
```

| Argument | Description |
|---|---|
| `--create` | Initialize GCP infrastructure resources. Runs by default if omitted. |

Resource creation runs in three ordered phases:

| Phase | Resources | Mode |
|---|---|---|
| 1 | Service Account + IAM bindings | Sequential (must be first) |
| 2 | Buckets, Artifact Registry, Pub/Sub, KMS, Cloud Build, Pre-VM Image, Kubernetes | **Parallel** |
| 3 | Cloud Function | Sequential (depends on Pub/Sub from Phase 2) |

- Each step is idempotent — existing resources are detected and skipped.
- If the GCP default subnet is not yet ready (common in new projects), Pre-VM Image creation retries automatically up to 5 times with a 30-second wait between attempts.
- Requires owner-level GCP permissions. Recommended to run from Cloud Shell.

> Future commands: `--delete`, `--update`

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
aigear-scheduler --version VERSION --step_names STEPS [--create]
```

| Argument | Default | Description |
|---|---|---|
| `--create` | — | Create GCP scheduler job. Runs by default if omitted. |
| `--version` | — | Pipeline version (required) |
| `--step_names` | — | Comma-separated step names, e.g. `fetch_data,training` (required) |

> `--version` and `--step_names` are required; the command will print a reminder and exit if either is missing.
>
> Future commands: `--delete`, `--update`, `--run`...

---

### `aigear-image`

Build Docker images for the pipeline (`Dockerfile.pl`) and/or model service (`Dockerfile.ms`), and optionally push to Artifact Registry.

```
aigear-image [--create]
             [--dockerfile_path PATH] [--build_context DIR]
             [--image_name NAME] [--image_version TAG]
             [--is_service] [--force] [--push]
```

| Argument | Default | Description |
|---|---|---|
| `--create` | — | Build and push Docker image(s) to Artifact Registry. Runs by default if omitted. |
| `--dockerfile_path` | `None` | Path to a specific Dockerfile. If omitted, builds both `Dockerfile.pl` and `Dockerfile.ms` |
| `--build_context` | `.` | Docker build context directory |
| `--image_name` | `None` | Override image name |
| `--image_version` | `latest` | Image version tag |
| `--is_service` | `false` | Mark image as a model service image (auto-set when building `Dockerfile.ms`) |
| `--force` | `false` | Rebuild even if the image already exists |
| `--push` | `false` | Push to Artifact Registry after build |

> Future commands: `--delete`, `--update`

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
aigear-env-schema [--generate] [--force]
```

| Argument | Description |
|---|---|
| `--generate` | Generate environment schema file. Runs by default if omitted. |
| `--force` | Regenerate the schema even if one already exists |

> Future commands: `--delete`, `--update`
