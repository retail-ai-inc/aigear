# Troubleshooting pipeline failures with logs

Use this guide when a scheduled or manual run fails and you need to find **why** using `aigear-logs`, Cloud Logging, or GCE serial output. For command flags, see [CLI Reference — `aigear-logs`](cli-reference.md#aigear-logs).

---

## Mental model: two failure layers

Aigear runs split into **infrastructure** (Cloud Function, VM, Docker, startup script) and **pipeline** (Python `aigear-task` inside the container).

| Layer | What failed | `log_source` in Cloud Logging | CLI `--log-source` |
|-------|-------------|-------------------------------|--------------------|
| **Infrastructure** | GCE insert, image pull/auth, startup script, GKE deploy inside startup | `cloud_function` | `cloud_function` |
| **Pipeline** | Business step code (`except` in your step module) | `ml_pipeline` | `ml_pipeline` |

**One-line rule**

- The task **never started normally** in the container (image missing, VM, `docker pull`, startup script) → query **`cloud_function`**.
- The container **is running `aigear-task`** and a Python step raised → query **`ml_pipeline`** first; use **`all`** if you are unsure or the run exited non-zero from startup (startup may still publish a VM `fatal_error` that appears on the CF side).

---

## Log source decision table

| What you want to investigate | Preferred `--log-source` | Typical `event` / fields | Notes |
|------------------------------|--------------------------|--------------------------|-------|
| VM creation failed (GCE insert) | `cloud_function` | `vm_creation_failed` | Written only by Cloud Function; VM never started |
| Docker pull / registry auth / startup script | `cloud_function` | **`vm_step_failed`** + `exit_code`, `docker_image` | VM `fatal_error` → Pub/Sub → CF |
| GKE deploy failed inside startup | `cloud_function` | **`vm_step_failed`**, `exit_code=deploy_failed` | Same path as Docker/startup failures |
| Python step exception in container | `ml_pipeline`, then `cloud_function` | `pipeline_step_failed`; terminal `vm_step_failed` with `exit_code=pipeline_failed` | Updated startup scripts also capture the failed command's output tail |
| Steps not executed after a failure | `cloud_function` | `pipeline_step_cancelled`, `failed_step`, `detail` | Concise output shows `CANCELLED` |
| Not sure / run already finished | **`all`** | Both sides | Non-zero step exit can still produce CF-side failure logs via startup `fatal_error` |

---

## Same name, different meaning: `pipeline_step_failed`

Historically, Cloud Function logged VM/orchestration failures as **`pipeline_step_failed`**, while the VM lifecycle logger used the **same event name** for real step failures. Filtering by `event` alone is ambiguous.

**After logging contract rollout (current code)**

| `event` | `log_source` | Meaning |
|---------|--------------|---------|
| **`vm_step_failed`** | `cloud_function` | VM command failure: Docker, startup, deploy, or a nonzero pipeline container exit (`exit_code=pipeline_failed`) |
| **`pipeline_step_failed`** | `ml_pipeline` | Container: business step failed |
| `vm_creation_failed` | `cloud_function` | CF could not create the VM |
| `task_validation_failed` / `pipeline_command_build_failed` | `cloud_function` | Task rejected before VM creation |
| `pipeline_step_cancelled` | `cloud_function` | Remaining task stopped after an earlier step failed |

**Legacy deployments (older Cloud Function)**

| `event` | `log_source` | Meaning |
|---------|--------------|---------|
| `pipeline_step_failed` | **`cloud_function`** | Infrastructure failure (same as today’s `vm_step_failed`) |
| `pipeline_step_failed` | `ml_pipeline` | Step failure (unchanged) |

`aigear-logs` discover and query accept **both** `vm_step_failed` and legacy `pipeline_step_failed` with `log_source=cloud_function`. Always pair **`event` + `log_source`** (or use `--log-source`) when building Console filters.

---

## Command templates

Run from the project directory with `env.json` / GCP auth configured.

**Discover runs for a calendar day** (scheduler timezone; converted to UTC internally):

```bash
aigear-logs --version <pipeline_version> --run-date <YYYY-MM-DD> --discovery-limit 2000
```

**Query by `run_id`**

```bash
# Quick step pass/fail (default: timeline; merges cloud_function + ml_pipeline)
aigear-logs --run-id <run_id>

# All steps, raw JSON
aigear-logs --run-id <run_id> --format full --limit 500

# Single step — timeline (default) or raw JSON
aigear-logs --run-id <run_id> --step training
aigear-logs --run-id <run_id> --format full --step training --limit 500

# Infrastructure / Docker / VM / startup (raw JSON)
aigear-logs --run-id <run_id> --format full --log-source cloud_function --limit 500

# Container step code (raw JSON)
aigear-logs --run-id <run_id> --format full --log-source ml_pipeline --limit 500

# Both sections in one invocation (requires --format full)
aigear-logs --run-id <run_id> --format full --log-source all --limit 500
```

| What you need | Command |
|---------------|---------|
| Which steps succeeded or failed | default (`--format concise`) |
| Full JSON for every step | `--format full --step all` |
| Logs for one step only | `--step <name>` |
| Infra vs container split | `--log-source cloud_function` or `ml_pipeline` |
| Business `logger.info` in Cloud Logging | `--step <name> --log-source ml_pipeline` after `gcp.logging=true` |

**Refresh local cache** (e.g. you queried mid-run, or first query returned nothing before logs landed):

```bash
aigear-logs --clear-cache
# then re-run the --run-id query
```

Empty query results are **not** cached; you should not need `--clear-cache` only because the first fetch was empty after the run completed.

## Failure details and cancelled steps

`aigear-task workflow` exits with code 1 when configuration lookup, module loading, or step execution fails. The VM startup script treats a nonzero container exit as terminal, publishes the failure, and deletes the VM instead of starting the next task. Cloud Function also stops the queue on task validation, command construction, or VM creation failures. Remaining tasks produce `pipeline_step_cancelled` with their run context, `failed_step`, and a readable reason.

The concise timeline shows failed and cancelled rows separately. A later CF `vm_step_failed` report with `exit_code=pipeline_failed` preserves the original Python failure summary. The **Failure details** section displays captured multiline errors and removes duplicate messages. Relevant fields are:

| Field | Meaning |
|-------|---------|
| `failure_stage` | Failed command stage, such as `registry_auth`, `docker_pull`, `pipeline_step`, `deployment_yaml_extract`, `gke_get_credentials`, or `kubectl_apply` |
| `command_exit_code` | Numeric exit code of the failed command |
| `error_message` | Last 8192 bytes of combined command output; a fallback message is used when output is empty |
| `error_output_truncated` | True when earlier output was omitted; concise output includes a truncation notice |

These fields require the updated Cloud Function startup script. They apply to newly created VMs; redeployment does not replace a script on an already running VM. Older failures may only have a summary. The complete successful command stream and output beyond the captured tail remain serial-console data unless business logging is enabled.

---

## Step failures when `gcp.logging=false`

| Source | What you get |
|--------|----------------|
| `ml_pipeline` lifecycle | `pipeline_step_failed` with `error_message` / `error_type` (truncated) |
| Per-module `logger.error` | Not sent as individual Cloud Logging entries; remains on stdout |
| Failed command output | Updated startup scripts include its last 8192 bytes in the CF `vm_step_failed` payload |
| Complete command stream | GCE **serial port** output (see below) |

`--format concise` shows lifecycle outcomes, cancellation, and captured failure output without `gcp.logging=true`. It does not expose individual business log entries or successful command output. Structured metrics require an optional `pipeline_step_result` from `emit_step_result()`.

For individual ERROR lines and `logger.info` metrics in Logging, set `aigear.gcp.logging` to `true`, rebuild the affected image, and query with `--step <name> --log-source ml_pipeline`. Stack traces are available only if the application logs or prints them; the lifecycle error summary does not generate a traceback.

---

## Cloud Logging Console fallbacks

Use when `aigear-logs` is unavailable or you need to inspect raw entries (including legacy text-only CF lines).

| Scenario | Suggested filter |
|----------|------------------|
| Run context / `run_id` | `jsonPayload.event="run_context_initialized"` and `jsonPayload.run_id="<run_id>"` |
| New infra failure | `jsonPayload.event="vm_step_failed"` and `jsonPayload.run_id="<run_id>"` |
| Legacy infra failure | `jsonPayload.event="pipeline_step_failed"` AND `jsonPayload.log_source="cloud_function"` |
| Missing Docker image | `jsonPayload.exit_code="docker_image_not_found"` |
| Old CF stderr text (no `log_source`) | `textPayload:"Pipeline step failed"` |
| Step failure in container | `jsonPayload.event="pipeline_step_failed"` AND `jsonPayload.log_source="ml_pipeline"` |

Restrict by time range and Cloud Function / Cloud Run resource labels for your project.

---

## Serial port (not in `aigear-logs`)

The complete startup script and command output remain on the VM **serial console**. Updated startup scripts additionally send the last 8192 bytes of a failed command's stdout/stderr through Pub/Sub to Cloud Logging. Successful output and serial `echo` markers are not automatically uploaded.

1. Open **Compute Engine → VM instances** → select the instance for the run.
2. **Serial port** (or “Connect to serial console”) — read `docker pull`, startup echoes, and task stdout.
3. Do **not** treat startup `echo` JSON with `event: vm_startup` as Cloud Logging entries; they are serial-only markers and are not part of the logging contract.

`aigear-logs` only queries **Cloud Logging** API entries.

---

## Deployment checklist (before trusting logs)

Complete these after infra or function changes; order matters for consistent behavior.

| Step | Action | Why |
|------|--------|-----|
| 1 | Grant **`roles/logging.logWriter`** to the runtime service account(s) used by Cloud Function and VM task | Without it, structured / lifecycle logs never reach Logging |
| 2 | `aigear-infra --update` | Re-applies Pub/Sub push ack **300s** and min retry **60s** (avoids duplicate CF invocations during long VM create) |
| 3 | Explicitly [redeploy Cloud Function source](#redeploy-existing-cloud-function-source) | `--create` skips existing source and `--update` does not redeploy it |
| 4 | `aigear-image --create --push --all` (or specify `--dockerfile_path`) | Includes current task exit behavior and the images referenced by scheduled tasks |
| 5 | `aigear-logs --clear-cache` once after deploy | Drops stale cached queries from mid-run debugging |
| 6 | Run [Verification Checklist](cli-reference.md#aigear-logs) items 1–6 | Confirms discover, split sources, and failure payloads |

## Redeploy existing Cloud Function source

After installing the Aigear version you intend to deploy, run the following from the project directory with authenticated GCP access and the target configuration selected. It uses the same deploy method as initial provisioning, renders fresh bundled source under `cloud_function/`, and deploys it to the configured function:

```bash
python - <<'PY'
from aigear.common.config import AppConfig
from aigear.infrastructure.gcp.function import CloudFunction

gcp = AppConfig.aigear().gcp
CloudFunction(
    function_name=gcp.cloud_function.function_name,
    region=gcp.location,
    entry_point="cronjobProcessPubSub",
    topic_name=gcp.pub_sub.topic_name,
    project_id=gcp.gcp_project_id,
    service_account=f"{gcp.iam.account_name}@{gcp.gcp_project_id}.iam.gserviceaccount.com",
    project_name=AppConfig.project_name() or "",
).deploy()
PY
```

On PowerShell, run the Python statements in a Python session instead of using the Bash heredoc wrapper. `aigear-infra --create` creates source only when the function is absent; for an existing function it adjusts timeout and invoker permissions. `--update` tunes Eventarc subscriptions but skips Cloud Function source updates. After redeployment, trigger a new run to verify cancellation and captured command errors.

---

## Quick reference: logging contract

| Write path | `log_source` | Must not use |
|------------|--------------|--------------|
| Cloud Function `writeCloudFunctionLog` | `cloud_function` | `ml_pipeline`; avoid queryable `console.error` |
| VM `emit_lifecycle_log` / `Logging.for_task` | `ml_pipeline` | `cloud_function` |
| Startup script serial `echo` | *(none — not in contract)* | Same values as Logging (`ml_pipeline` / `cloud_function`) |

---

## Related documentation

- [CLI Reference — `aigear-logs`](cli-reference.md#aigear-logs) — `--step`, `--format concise`, `--log-source`, cache behavior, verification checklist
- [CLI Reference — `aigear-infra`](cli-reference.md#aigear-infra) — Eventarc ack/retry defaults
