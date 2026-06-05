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
| Python step exception in container | `ml_pipeline` | `pipeline_step_started` / **`pipeline_step_failed`** (logger `aigear-task`) | Full stack traces need `gcp.logging=true` |
| Not sure / run already finished | **`all`** | Both sides | Non-zero step exit can still produce CF-side failure logs via startup `fatal_error` |

---

## Same name, different meaning: `pipeline_step_failed`

Historically, Cloud Function logged VM/orchestration failures as **`pipeline_step_failed`**, while the VM lifecycle logger used the **same event name** for real step failures. Filtering by `event` alone is ambiguous.

**After logging contract rollout (current code)**

| `event` | `log_source` | Meaning |
|---------|--------------|---------|
| **`vm_step_failed`** | `cloud_function` | Infrastructure: Docker, startup script, deploy, etc. |
| **`pipeline_step_failed`** | `ml_pipeline` | Container: business step failed |
| `vm_creation_failed` | `cloud_function` | CF could not create the VM |

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
# Quick step pass/fail (timeline; merges cloud_function + ml_pipeline)
aigear-logs --run-id <run_id> --format concise

# All steps, raw JSON (default --format full, --step all)
aigear-logs --run-id <run_id> --limit 500

# Single step — raw logs or one-line timeline
aigear-logs --run-id <run_id> --step training --limit 500
aigear-logs --run-id <run_id> --format concise --step training

# Infrastructure / Docker / VM / startup
aigear-logs --run-id <run_id> --log-source cloud_function --limit 500

# Container step code
aigear-logs --run-id <run_id> --log-source ml_pipeline --limit 500

# Both sections in one invocation (labeled cloud_function, then ml_pipeline)
aigear-logs --run-id <run_id> --log-source all --limit 500
```

| What you need | Command |
|---------------|---------|
| Which steps succeeded or failed | `--format concise` |
| Full JSON for every step | default (`--step all`, `--format full`) |
| Logs for one step only | `--step <name>` |
| Infra vs container split | `--log-source cloud_function` or `ml_pipeline` |
| Business `logger.info` in Cloud Logging | `--step <name> --log-source ml_pipeline` after `gcp.logging=true` |

**Refresh local cache** (e.g. you queried mid-run, or first query returned nothing before logs landed):

```bash
aigear-logs --clear-cache
# then re-run the --run-id query
```

Empty query results are **not** cached; you should not need `--clear-cache` only because the first fetch was empty after the run completed.

---

## Step failures when `gcp.logging=false`

| Source | What you get |
|--------|----------------|
| `ml_pipeline` lifecycle | `pipeline_step_failed` with `error_message` / `error_type` (truncated) |
| Per-module `logger.error` | **Not** in Cloud Logging — stdout only |
| Detail | GCE **serial port** output (see below) |

`--format concise` still shows lifecycle outcomes (including infra `vm_step_failed` from `cloud_function`) without `gcp.logging=true`. It does **not** surface per-module business logs or training metrics — only lifecycle **DETAIL** and optional `pipeline_step_result` from `emit_step_result()`.

For full ERROR lines, `logger.info` metrics, and stack traces in Logging, set `aigear.gcp.logging` to `true` in `env.json` for that environment (staging is a common choice), then query with `--step <name> --log-source ml_pipeline`.

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

Startup script output, `docker pull`, and `aigear-task` stdout/stderr when `gcp.logging=false` often appear only on the VM **serial console**, not in Cloud Logging.

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
| 3 | Redeploy **Cloud Function** (via `aigear-infra --create` / `--update` when function config changes) | `vm_step_failed`, `log_source=cloud_function`, and removed `console.error` paths require the deployed `index.js` |
| 4 | `aigear-image --create --push` (or your image workflow) | Fixes `docker_image_not_found` and related `exit_code`s |
| 5 | `aigear-logs --clear-cache` once after deploy | Drops stale cached queries from mid-run debugging |
| 6 | Run [Verification Checklist](cli-reference.md#aigear-logs) items 1–6 | Confirms discover, split sources, and failure payloads |

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
