# CLI Reference

All CLI entry points are installed as standalone commands by `pip install aigear`.

| Command | Description |
|---|---|
| `aigear-init` | Initialize a new project scaffold |
| `aigear-gcp-infra` | Create GCP infrastructure (buckets, IAM, Pub/Sub, schedulers) |
| `aigear-task` | Run a pipeline step or start a gRPC model service |
| `aigear-scheduler` | Create a Cloud Scheduler job for pipeline steps |
| `aigear-image` | Build and optionally push Docker images to Artifact Registry |
| `aigear-model` | Generate YAML and manage the lifecycle of a gRPC model service (deploy, update, delete, status) |
| `aigear-env-schema` | Auto-generate a Pydantic schema from `env.json` |
| `aigear-kms-env` | Encrypt or decrypt `env.json` using Cloud KMS |

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

### `aigear-task`

Run a pipeline step or start a gRPC model service. The step module path and model class path are resolved automatically from `env.json`.

```
aigear-task <subcommand> [options]
```

#### Subcommand: `workflow`

Run a single named pipeline step locally.

```
aigear-task workflow --version VERSION --step STEP_NAME
```

| Argument | Description |
|---|---|
| `--version` | Pipeline version (e.g., `logistic_regression`) |
| `--step` | Step name as defined in `env.json` (e.g., `fetch_data`). The full module path is looked up from `env.json`. |

#### Subcommand: `grpc`

Start a gRPC model serving server. The model class path is resolved from `env.json`.

```
aigear-task grpc --version VERSION
```

| Argument | Description |
|---|---|
| `--version` | Pipeline version (e.g., `logistic_regression`) |

---

### `aigear-scheduler`

Manage Cloud Scheduler jobs that trigger pipeline steps via Pub/Sub.

```
aigear-scheduler <command> --version VERSION [--step_names STEPS] [--env ENV]
```

| Command | `--step_names` required | Description |
|---|---|---|
| `--create` | yes | Create a new scheduler job (skips if already exists) |
| `--update` | yes | Update schedule and message body of an existing job |
| `--delete` | — | Delete the scheduler job |
| `--status` | — | Print the current status of the scheduler job |
| `--list` | — | List scheduler jobs filtered by name |
| `--run` | — | Manually trigger the scheduler job immediately |
| `--pause` | — | Pause the scheduler job (stops automatic execution) |
| `--resume` | — | Resume a paused scheduler job |

| Argument | Default | Description |
|---|---|---|
| `--version` | — | Pipeline version (required for all commands) |
| `--step_names` | — | Comma-separated step names, e.g. `fetch_data,training` (required for `--create` / `--update`) |
| `--env` | `staging` | Deployment environment for model service: `staging` or `production` |

**Examples**

```bash
# Create a scheduler job for two pipeline steps
aigear-scheduler --create --version logistic_regression --step_names fetch_data,training

# Update the schedule or message body
aigear-scheduler --update --version logistic_regression --step_names fetch_data,training --env production

# Trigger an immediate run without waiting for the cron schedule
aigear-scheduler --run --version logistic_regression

# Pause / resume
aigear-scheduler --pause  --version logistic_regression
aigear-scheduler --resume --version logistic_regression
```

> The scheduler job name, cron schedule, and Pub/Sub topic are read from the `scheduler` block in `env.json` for the given `--version`.

---

### `aigear-image`

Build Docker images for the pipeline (`Dockerfile.pl`) and/or model service (`Dockerfile.ms`), and optionally push to Artifact Registry.

```
aigear-image [--create]
             [--dockerfile_path PATH] [--build_context DIR]
             [--image_name NAME] [--image_version TAG]
             [--is_service] [--force] [--push]
```

At least one of `--create` or `--push` is required. They can be combined to build then push in a single command.

| Argument | Default | Description |
|---|---|---|
| `--create` | — | Build the Docker image(s) |
| `--push` | — | Push the Docker image(s) to Artifact Registry. Can be used alone (push-only) or together with `--create` (build then push). |
| `--dockerfile_path` | `None` | Path to a specific Dockerfile. If omitted, operates on both `Dockerfile.pl` and `Dockerfile.ms` |
| `--build_context` | `.` | Docker build context directory |
| `--is_service` | `false` | Mark image as a model service image (auto-set when building `Dockerfile.ms`) |

> Future commands: `--delete`, `--update`

---

### `aigear-model`

Manage the full lifecycle of a gRPC model service: generate the Kubernetes deployment YAML, deploy, update, delete, or check status. Works with local Kubernetes (Docker Desktop) and GCP (staging / production). The model class path is resolved automatically from `env.json`.

```
aigear-model --version VERSION {--local | --staging | --production}
             {--yaml | --deploy | --update | --delete | --status}
             [--service_ports PORTS] [--replicas N] [--port PORT]
```

**Environment (required, mutually exclusive)**

| Argument | Description |
|---|---|
| `--local` | Target local Kubernetes (Docker Desktop) |
| `--staging` | Target GCP staging environment |
| `--production` | Target GCP production environment |

**Operation (required, mutually exclusive)**

| Argument | Description |
|---|---|
| `--yaml` | Generate (or overwrite) the deployment YAML file and exit |
| `--deploy` | Create the YAML if it does not yet exist, then deploy the service |
| `--update` | Create the YAML if it does not yet exist, then re-apply with any new parameters |
| `--delete` | Switch to the target context and delete the service deployment |
| `--status` | Switch to the target context and show the current deployment status |

**Optional parameters**

| Argument | Default | Description |
|---|---|---|
| `--version` | — | Pipeline version (required for all operations) |
| `--service_ports` | `50051` | Internal container port(s) |
| `--replicas` | `1` | Number of service replicas |
| `--port` | `50051` | External service port |

> **Auto-force:** Passing any of `--service_ports`, `--replicas`, or `--port` automatically overwrites the existing YAML, so the new parameters take effect immediately. `--yaml` always overwrites.

**Examples**

```bash
# Generate YAML for local environment
aigear-model --version logistic_regression --local --yaml

# Deploy to local Kubernetes (creates YAML if absent)
aigear-model --version logistic_regression --local --deploy

# Update with a new replica count (overwrites YAML automatically)
aigear-model --version logistic_regression --staging --update --replicas 3

# Check deployment status on GCP production
aigear-model --version logistic_regression --production --status

# Delete the local deployment
aigear-model --version logistic_regression --local --delete
```

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

---

### `aigear-kms-env`

Encrypt or decrypt `env.json` using Cloud KMS. The default ciphertext path is `kms/<env>/<env>-env.bin` (e.g. `kms/staging/staging-env.bin`).

```
aigear-kms-env {--encrypt | --decrypt}
               [--environment {staging,production}]
               [--input PATH] [--output PATH]
               [--project-id ID] [--location LOC] [--keyring NAME] [--key NAME]
```

| Argument | Default | Description |
|---|---|---|
| `--encrypt` | — | Encrypt `env.json` to a `.bin` ciphertext file. Mutually exclusive with `--decrypt`. |
| `--decrypt` | — | Decrypt a `.bin` ciphertext file to `env.json`. Mutually exclusive with `--encrypt`. |
| `--environment` | `staging` | Target environment (`staging` or `production`). Determines the default ciphertext path. |
| `--input` | `None` | Override the input file path. |
| `--output` | `None` | Override the output file path. |
| `--project-id` | `None` | GCP project ID. Falls back to `env.json` if omitted. |
| `--location` | `None` | KMS location (e.g. `asia-northeast1`). Falls back to `env.json` if omitted. |
| `--keyring` | `None` | KMS keyring name. Falls back to `env.json` if omitted. |
| `--key` | `None` | KMS key name. Falls back to `env.json` if omitted. |

> When decrypting (before `env.json` exists), provide `--project-id`, `--location`, `--keyring`, and `--key` explicitly, since there is no `env.json` to fall back on.
