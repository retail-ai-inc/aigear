# CLI Reference

All CLI entry points are installed as standalone commands by `pip install aigear`.

| Command | Description |
|---|---|
| `aigear-init` | Initialize a new project scaffold |
| `aigear-infra` | Create infrastructure (buckets, IAM, Pub/Sub, schedulers) |
| `aigear-task` | Run a pipeline step or start a gRPC model service |
| `aigear-scheduler` | Create a Cloud Scheduler job for pipeline steps |
| `aigear-image` | Build and optionally push Docker images to Artifact Registry |
| `aigear-model` | Generate YAML and manage the lifecycle of a gRPC model service (deploy, update, delete, status) |
| `aigear-env-schema` | Auto-generate a Pydantic schema from `env.json` |
| `aigear-kms-env` | Encrypt or decrypt `env.json` using Cloud KMS |
| `aigear-logs` | Discover pipeline runs and query Cloud Logging by `run_id` |

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

> **Git pre-commit hook**: `aigear-init` installs a pre-commit hook in the new project's `.git/hooks/` directory. It blocks commits when `env.json` exists but no ciphertext is present under `kms/`, or when any ciphertext is older than `env.json`. It does not inspect staged files. The generated `.gitignore` excludes `env.json`; do not force-add plaintext configuration.

---

### `aigear-infra`

Read `env.json` and manage all defined GCP resources (buckets, Pub/Sub topics, Cloud Function, Artifact Registry, KMS, Cloud Build trigger, GKE cluster, service accounts, etc.).

```
aigear-infra {--create | --update | --delete | --status}
```

| Argument | Description |
|---|---|
| `--create` | Initialize GCP infrastructure resources. |
| `--update` | Update resources that support update: Cloud Build trigger (config) and Kubernetes cluster (node count, autoscaling). Resources that do not support update are skipped with a log message. |
| `--delete` | Delete GCP infrastructure resources (see deletion phases below). Note: Cloud KMS keyrings cannot be deleted (a GCP platform limitation); key versions are scheduled for destruction but the keyring itself persists. |
| `--status` | Query and display the live state of all GCP infrastructure resources (including Eventarc Pub/Sub trigger when applicable). |

**`--create`** runs in three ordered phases:

| Phase | Resources | Mode |
|---|---|---|
| 1 | Service Account + IAM bindings | Sequential (must be first) |
| 2 | Buckets, Artifact Registry, Pub/Sub topic, KMS, Cloud Build, Pre-VM Image, Kubernetes, Cloud Function (deploy only, no Pub/Sub trigger) | **Parallel** |
| 3 | Eventarc Pub/Sub trigger | Sequential (requires Pub/Sub topic **and** Cloud Function from Phase 2) |

Phase 3 runs only when **both** `gcp.pub_sub.on` and `gcp.cloud_function.on` are `true`. The trigger creates the Pub/Sub **subscription** that delivers topic messages to the function (per [Cloud Run Pub/Sub triggers](https://cloud.google.com/run/docs/triggering/pubsub-triggers#gcloud)). Aigear sets the push subscription **ack deadline to 300s** and **minimum retry backoff to 60s** (Eventarc defaults are ~10s, which causes duplicate Cloud Function invocations during VM insert). **`aigear-infra --update`** re-applies these settings when the trigger already exists.

**`--delete`** runs in reverse order:

| Phase | Resources | Mode |
|---|---|---|
| 1 | Eventarc Pub/Sub trigger (if both `pub_sub` and `cloud_function` enabled), then Cloud Function | Sequential |
| 2 | Buckets, Artifact Registry, Pub/Sub topic, KMS, Cloud Build, Pre-VM Image, Kubernetes | **Parallel** |
| 3 | Service Account | Sequential (last) |

Pub/Sub topic deletion removes any remaining subscriptions on that topic before the topic itself is deleted.

- Cloud Function and GKE deletion waits for completion before service account deletion. The wait limits are 600 seconds for Cloud Function and 1800 seconds for GKE. A timeout means completion is unconfirmed and the cleanup is reported as failed.
- If Eventarc deletion fails, Cloud Function and Pub/Sub topic deletion are blocked. Other independent cleanup continues. Any failed, blocked, or incomplete cleanup retains the service account and appears in the final summary.
- Each resource's `on` flag controls whether it is managed. Disabled resources are skipped. Eventarc is managed only when both `pub_sub.on` and `cloud_function.on` are enabled; cleanup does not scan the project for other dependencies.
- Creation skips existing resources while re-applying required settings and IAM bindings. Deletion skips resources confirmed to be absent. Permission errors, timeouts, and other query failures are reported as errors.
- Preflight checks verify gcloud availability, an active account, and the configured project. Login and project changes are rechecked before resource operations start. Login has a 300-second timeout; other preflight commands have a 30-second timeout.
- `--create`, `--update`, and `--delete` exit with code **0** on success and **1** on any operation or preflight failure, including blocked phases. Independent steps continue where possible so the summary includes their results. Invalid arguments retain argparse's exit code **2**.
- `--status` reports `EXISTS`, `NOT_FOUND`, `PARTIAL`, or `ERROR`. A KMS keyring without its configured key is `PARTIAL` and counts toward the partial-resource total. Per-resource query errors appear in the table; the status query itself keeps its existing exit-code behavior.
- If the GCP default subnet is not yet ready, Pre-VM Image creation makes up to 5 attempts per zone with a 30-second wait between attempts, then tries the next fallback zone (`a`, `b`, `c`). Capacity exhaustion moves directly to the next zone; unrelated errors propagate.
- Requires owner-level GCP permissions. Recommended to run from Cloud Shell.
- Existing Cloud Function source is not redeployed: `--create` re-applies timeout and invoker permissions, while `--update` skips function updates. To deploy revised source, follow [the explicit redeployment procedure](troubleshooting-logs.md#redeploy-existing-cloud-function-source).

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

Successful workflow execution exits with code **0**. Missing pipeline/step configuration, module loading failures, and step exceptions exit with code **1**. A scheduled step failure stops the queue; subsequent steps are logged as `pipeline_step_cancelled` and appear as `CANCELLED` in `aigear-logs`.

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

Manage the full lifecycle of Docker images for the pipeline (`Dockerfile.pl`) and model service (`Dockerfile.ms`): build, delete, or re-tag locally and optionally sync to Artifact Registry.

```
aigear-image {--create | --delete | --clear | --retag}
             [--push]
             [--dockerfile_path PATH] [--build_context DIR]
             [--is_service] [--all]
             [--src_tag TAG] [--target_tag TAG]
```

One action (`--create`, `--delete`, `--clear`, `--retag`) is required. `--create` also requires at least one of `--dockerfile_path PATH` or `--all`; omitting both exits with code **2** before building. When both are provided, `--all` takes precedence. Other actions do not require either scope argument. `--push` syncs the operation to Artifact Registry after the local step succeeds.

**Actions (mutually exclusive)**

| Argument | Description |
|---|---|
| `--create` | Build the Docker image locally |
| `--delete` | Remove the Docker image with the current tag locally |
| `--clear` | Delete **all** local images for this repository (every tag), then run `docker image prune -f` to clean up dangling images on the host (not limited to this project). With `--push`, also remove that image and **all** its tags from Artifact Registry. |
| `--retag` | Tag an existing local image with a new tag (requires `--src_tag` and `--target_tag`) |

**Scope modifiers**

| Argument | Default | Description |
|---|---|---|
| `--all` | `false` | Operate on both `Dockerfile.pl` (pipeline) and `Dockerfile.ms` (service) in one command |
| `--dockerfile_path` | `None` | Path to a specific Dockerfile. `Dockerfile.ms` automatically implies `--is_service`; `Dockerfile.pl` implies pipeline |
| `--is_service` | `false` | Target the model service image. Ignored when `--dockerfile_path` is `Dockerfile.pl` or `Dockerfile.ms` (inferred automatically) |
| `--build_context` | `.` | Docker build context directory (used with `--create`) |

**Remote sync**

| Argument | Description |
|---|---|
| `--push` | After the local operation succeeds, sync to Artifact Registry (push image, delete remote tag, or add remote tag) |

**Re-tag arguments**

| Argument | Description |
|---|---|
| `--src_tag` | Source tag (required with `--retag`) |
| `--target_tag` | Destination tag (required with `--retag`) |

**Scope resolution (without `--all`)**

| `--dockerfile_path` | `--is_service` | Target |
|---|---|---|
| `Dockerfile.ms` | any | service image |
| `Dockerfile.pl` | any | pipeline image |
| custom path | `false` (default) | pipeline image |
| custom path | `true` | service image |
| omitted (except `--create`) | `false` (default) | pipeline image |
| omitted (except `--create`) | `true` | service image |

Use `--create --dockerfile_path Dockerfile.pl`, `--create --dockerfile_path Dockerfile.ms`, or `--create --all` to build. `--is_service` alone does not satisfy the build scope requirement. `--push` requires an action; to push an already-built image without rebuilding, authenticate Docker and run `docker push <full-image-path>:<tag>` directly. Image operation failures currently print an error without reliably returning a nonzero CLI exit code.

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

`--version` is required for every operation. Omitting it exits with code **2** before YAML generation or Kubernetes operations. Deployment, update, deletion, and status operations proceed only after the local or GCP context-switch command succeeds; a nonzero exit or timeout aborts the operation. Failures from the subsequent kubectl operation are not consistently propagated as nonzero exits; inspect command output afterward.

When `model_service.model_class_path` is configured, YAML is generated in the model module directory and includes `aigear-task grpc --version VERSION` as the container command (using `venv_ms` when configured). If the class path or the entire `model_service` section is omitted, YAML is generated at the project root as `grpc_deployment_<environment>.yaml`, without container `command` or `args`. In that case, the image must provide its own working startup configuration (`ENTRYPOINT`/`CMD`); YAML generation alone does not ensure a gRPC service will start.

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

Manage the lifecycle of the Pydantic schema file generated from `env.json`.

```
aigear-env-schema {--generate | --delete | --show} [--force]
```

| Argument | Description |
|---|---|
| `--generate` | Generate environment schema file from `env.json` |
| `--delete` | Delete the generated schema file |
| `--show` | Print the current schema file content |
| `--force` | Force regenerate even if the schema already exists (used with `--generate`) |

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

---

### `aigear-logs`

Discover run IDs for a given date/version and then query logs by `run_id`. **Run discovery always scans Cloud Logging.** **Log queries by `run_id`** use a local cache (3 hours TTL) after the first fetch — pipeline logs are immutable once a run finishes. Use `--clear-cache` if you queried mid-run and need a fresh read.

```
aigear-logs [--version VERSION --run-date YYYY-MM-DD]
            [--run-id RUN_ID]
            [--step STEP_NAME]
            [--format {full,concise}]
            [--log-source {cloud_function,ml_pipeline,all}]
            [--time-zone IANA_TZ]
            [--limit N]
            [--clear-cache]
```

| Argument | Default | Description |
|---|---|---|
| `--version` | — | Pipeline version used during discovery mode |
| `--run-date` | — | Date interpreted in scheduler timezone, then converted to UTC for querying |
| `--run-id` | — | Direct query mode; skips discovery |
| `--step` | `all` | Step scope: omit or `all` = every step; a name (e.g. `training`) = that step only |
| `--format` | `concise` | `concise` = step timeline summary (default); `full` = raw JSON log stream |
| `--log-source` | *(none)* | Narrow by layer: `cloud_function`, `ml_pipeline`, or `all` (both sections). Omitted = one combined query with no source filter |
| `--time-zone` | scheduler `time_zone` from `env.json` | Override timezone used to interpret `--run-date` |
| `--limit` | `200` | Max logs returned per query (use `500` when tracing failures) |
| `--discovery-limit` | `1000` | Max log entries scanned per discover query |
| `--clear-cache` | `false` | Remove local log query cache and exit |

**Step scope (`--step`) and output format (`--format`)**

| Command | Output |
|---|---|
| `aigear-logs --run-id <id>` | Step timeline for the full pipeline (default `--format concise`) |
| `aigear-logs --run-id <id> --step all` | Same as above (explicit) |
| `aigear-logs --run-id <id> --step training` | One-line timeline for `training` (default concise) |
| `aigear-logs --run-id <id> --format full` | Raw JSON for all steps |
| `aigear-logs --run-id <id> --format full --step training` | Raw JSON for the `training` step only |

`--format concise` merges `cloud_function` and `ml_pipeline` lifecycle events into one table. Infrastructure failures (`vm_step_failed`, `vm_creation_failed`, legacy CF `pipeline_step_failed`) and container failures (`pipeline_step_failed` on `ml_pipeline`) appear in the **DETAIL** column. When a step calls `emit_step_result()`, structured metrics show in **DETAIL** for successful steps.

Task validation and command-building failures also appear as `FAILED`. After a step failure, unexecuted steps appear as `CANCELLED` with the failed step in **DETAIL**. Cancellation does not replace an already running, successful, or failed step. A later CF `vm_step_failed` entry with `exit_code=pipeline_failed` preserves an earlier Python failure summary.

Failed rows are followed by **Failure details**, including captured multiline output, `failure_stage`, and `command_exit_code` when available. Command output is limited to the last **8192 bytes**, with a truncation notice when exceeded. Duplicate error messages are shown once. If no output was captured, the CLI explains that only the failure summary is available and that VM command details require the updated Cloud Function startup script.

Example timeline:

```
=== Step timeline (run_id=26fe3e695ca7d8c6) ===
STEP            STATUS   STARTED (UTC)              FINISHED (UTC)             DURATION  DETAIL
fetch_data      OK       2026-06-03T06:33:06Z       2026-06-03T06:33:11Z       5s
preprocessing   OK       2026-06-03T06:34:39Z       2026-06-03T06:34:43Z       4s
training        OK       2026-06-03T06:36:11Z       2026-06-03T06:36:16Z       5s
model_service   FAILED   —                          —                          —         docker_image_not_found (...)
```

**Examples**

```bash
# Default: step timeline for all steps
aigear-logs --run-id <run_id>

# Raw JSON for all steps
aigear-logs --run-id <run_id> --format full --limit 500

# Single step — timeline (default) or raw JSON
aigear-logs --run-id <run_id> --step training
aigear-logs --run-id <run_id> --format full --step training --limit 500

# Split infra vs container sections (requires --format full)
aigear-logs --run-id <run_id> --format full --log-source all --limit 500
```

**Logging contract (`log_source` + `event`)**

| Layer | `log_source` | Typical `event` | Query with |
|-------|--------------|-----------------|------------|
| Cloud Function / VM orchestration | `cloud_function` | `run_context_initialized`, `vm_created`, `vm_creation_failed`, **`vm_step_failed`**, `pipeline_step_cancelled`, `task_validation_failed`, `pipeline_command_build_failed` | `--log-source cloud_function` |
| Container `aigear-task` | `ml_pipeline` | `pipeline_step_started`, **`pipeline_step_failed`**, … | `--log-source ml_pipeline` |

Rules:

- **Docker pull, authentication, and deploy command** failures are logged under **`cloud_function`** as **`vm_step_failed`** (includes `exit_code`, `docker_image`, and captured command output). VM creation failures use **`vm_creation_failed`**.
- **Python step exceptions** produce **`pipeline_step_failed`** under **`ml_pipeline`** (`error_message` / `error_type` on lifecycle; strings truncated to 2KB). The updated startup script also reports the nonzero container exit under **`cloud_function`** as **`vm_step_failed`**, with `exit_code=pipeline_failed` and the last 8192 bytes of command output, even when `gcp.logging=false`.
- Legacy deployments may still show `pipeline_step_failed` with `log_source=cloud_function` for VM errors; discover and query accept both.
- VM serial / startup `echo` is **not** in Cloud Logging — use GCE serial port for raw stdout.

**Failure troubleshooting**

See **[Troubleshooting pipeline logs](troubleshooting-logs.md)** for the full guide: `cloud_function` (Docker / VM / startup script) vs `ml_pipeline` (container step), disambiguating legacy vs current `pipeline_step_failed`, command templates, Cloud Console filters, serial port fallback, and the post-deploy checklist.

| Symptom | Command |
|---------|---------|
| Find runs for a day | `aigear-logs --version <v> --run-date <YYYY-MM-DD> [--discovery-limit 2000]` |
| Quick step pass/fail (default) | `aigear-logs --run-id <id>` |
| All step logs (raw JSON) | `aigear-logs --run-id <id> --format full --limit 500` |
| One step only | `aigear-logs --run-id <id> --step <name>` or add `--format full` for JSON |
| Infra / Docker failure | `aigear-logs --run-id <id> --format full --log-source cloud_function --limit 500` |
| Step code failure | `aigear-logs --run-id <id> --format full --log-source ml_pipeline --limit 500` |
| Unsure / both layers | `aigear-logs --run-id <id> --format full --log-source all --limit 500` |
| Queried mid-run, need fresh logs | `aigear-logs --clear-cache` then re-query |

Empty query results are **not** cached. After a run finishes, logs are stable for 3 hours in the local cache.

**Pipeline logging (`env.json` → `aigear.gcp.logging`)**

| Environment | Behavior |
|---|---|
| Local `aigear-task` | `Logging.for_task()` → stdout only |
| VM + `gcp.logging=false` | stdout with run fields; lifecycle events to Cloud Logging |
| VM + `gcp.logging=true` | stdout + all task logs to Cloud Logging (`log_source=ml_pipeline`, `run_id`, …) |

Lifecycle events (`pipeline_step_started` / `finished` / `failed`, `vm_step_failed`, …) are written to Cloud Logging for runs with installed run context and drive `--format concise`. **Business logs** are sent individually to Cloud Logging when `gcp.logging=true`. Otherwise they stay on the VM serial port, except that the updated startup script captures the tail of failed command output in the CF failure payload. Use `--step <name> --log-source ml_pipeline` to read individual business entries after enabling logging.

Optional **`emit_step_result(ctx, result)`** (from `aigear.common.lifecycle_log`) publishes a `pipeline_step_result` lifecycle event with a structured `result` dict (e.g. `{"accuracy": 0.92, "rows": 1000}`). The concise timeline shows these values in **DETAIL** for OK steps. Pipelines are not required to call it; use it when you want key metrics visible without scanning raw JSON.

**Flow**

1. If `--run-id` is provided, query logs directly by `run_id` (or print a step timeline when `--format concise`).
2. Otherwise discover run IDs for `--version` + `--run-date` (0/1/N branch):
   - 0: exit with "no runs"
   - 1: auto-select and query (or timeline)
   - N: interactive selection, then query (or timeline)
3. `--step` filters scope; default `all` does not filter. `--format concise` (default) prints the merged step timeline; `--format full` prints raw JSON.

**Verification Checklist (post-deploy)**

1. Trigger scheduler once, then filter Cloud Logging with `jsonPayload.event="run_context_initialized"` and verify `log_source=cloud_function` plus a non-empty `run_id`.
2. Run `aigear-logs --version <v> --run-date <YYYY-MM-DD>` and confirm run discovery works.
3. Query by source:
   - `aigear-logs --run-id <id> --log-source cloud_function`
   - `aigear-logs --run-id <id> --log-source ml_pipeline`
   - or `aigear-logs --run-id <id> --log-source all` to print both sections in one run.
4. Publish an invalid JSON message to the topic and verify:
   - Cloud Function logs include `invalid_json_message`
   - a terminal `task_invalid` error payload is published
   - no new VM insert is triggered
   - any follow-up error payload invocation exits at `vm_step_failed` without VM creation.
5. After a VM/startup failure (e.g. `docker_image_not_found`), verify `vm_step_failed` in Cloud Logging includes `jsonPayload.run_id`, `log_source=cloud_function`, `exit_code`, and `docker_image` so `aigear-logs --run-id <id> --log-source cloud_function` shows the failure.
6. After a step code failure (with `gcp.logging=false` is enough for lifecycle), verify `pipeline_step_failed` with `log_source=ml_pipeline` includes `error_message` and `error_type`; with `gcp.logging=true`, also expect per-module business ERROR logs.
