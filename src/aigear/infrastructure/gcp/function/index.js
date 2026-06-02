import crypto from 'node:crypto';
import { google } from 'googleapis';
import functions from '@google-cloud/functions-framework';
import { Logging } from '@google-cloud/logging';

// ─── Config ───────────────────────────────────────────────────────────────────

const CONFIG = {
  projectId: '{{PROJECTID}}',
  projectName: '{{PROJECTNAME}}',
  region:    '{{REGION}}',
  topicName: '{{TOPICSNAME}}',
  // serviceAccount is not hardcoded — fetched at runtime from the metadata server

  vm: {
    // Machine types
    defaultCpuSpec: 'e2-medium',
    defaultGpuSpec: 'n1-standard-4',

    // GPU hardware
    defaultGpuType:  'nvidia-tesla-t4',
    defaultGpuCount: 1,

    // Custom images baked by pre_vm_image.py (pre-installed: Docker, gcloud, kubectl)
    // CPU image: Docker CE + gcloud CLI + kubectl
    defaultCpuImage: 'ml-training-cpu-image',
    // GPU image: Docker CE + NVIDIA Container Toolkit + gcloud CLI + kubectl
    defaultGpuImage: 'ml-training-gpu-image',

    // Disk sizes (GB)
    // ml-training-cpu-image: ~50 GB  → minimum 50 GB
    // ml-training-gpu-image: ~100 GB → minimum 100 GB
    defaultCpuDisk: '50',
    defaultGpuDisk: '100',
    minCpuDisk:     50,
    minGpuDisk:     100,

    // Seconds to wait before self-deleting the VM after the job finishes
    sleepBeforeDelete: 10,
  },
};

CONFIG.fallbackZones = ['a', 'b', 'c'].map(s => `${CONFIG.region}-${s}`);

let _structuredLog = null;

function getStructuredLog() {
  if (!_structuredLog) {
    const logging = new Logging({ projectId: CONFIG.projectId });
    _structuredLog = logging.log('cronjobProcessPubSub');
  }
  return _structuredLog;
}

async function writeStructuredLog(payload, severity = 'INFO') {
  const log = getStructuredLog();
  const entry = log.entry({ severity }, payload);
  await log.write(entry);
}

function buildRunLogFields(task = {}) {
  if (!task || typeof task !== 'object') {
    return {};
  }
  return {
    run_id: task.run_id || undefined,
    run_started_at_utc: task.run_started_at_utc || undefined,
    pipeline_version: task.pipeline_version || undefined,
    step_name: task.step_name || undefined,
    project_name: task.project_name || undefined,
    instance_name: task.instance_name || undefined,
    zone: task.zone || undefined,
  };
}

async function writeCloudFunctionLog({
  event,
  message,
  severity = 'INFO',
  task = null,
  extra = {},
}) {
  const payload = {
    log_source: 'cloud_function',
    event,
    message,
    ...buildRunLogFields(task || {}),
    ...extra,
  };
  try {
    await writeStructuredLog(payload, severity);
  } catch (err) {
    // Keep one stderr fallback in case Cloud Logging write itself fails.
    console.error(`Failed to write structured CF log: ${err.message}`);
  }
}

// ─── Constants ────────────────────────────────────────────────────────────────

// Terminal Pub/Sub message values
const MSG = {
  PIPELINE_DONE: 'createVMDate',
  EMPTY_QUEUE:   '[]',
};

// Zone-exhausted error keywords (covers both stockout and GPU-not-available-in-zone)
const EXHAUSTED_CODES = [
  'EXHAUSTED',
  'stockout',
  'ZONE_RESOURCE_POOL_EXHAUSTED',
  'acceleratorTypes',   // GPU type absent from a zone (404)
];

// Non-retryable VM errors — retrying other zones or redelivering the message cannot help
const FATAL_VM_ERROR_CODES = [
  'NOT_FOUND',
  'INVALID_ARGUMENT',
  'INVALID_ARG_VALUE',
];

// ─── Utilities ────────────────────────────────────────────────────────────────

const INSERT_OPERATION_WAIT_MS = 20000;

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function deriveRunId(projectName, pipelineVersion, runStartedAtUtc) {
  const normalizedProjectName = projectName || '';
  return crypto
    .createHash('sha256')
    .update(`${normalizedProjectName}:${pipelineVersion}:${runStartedAtUtc}`, 'utf8')
    .digest('hex')
    .slice(0, 16);
}

function resolveStepName(task) {
  return task.step_name || 'model_service';
}

function sanitizeLabelValue(value) {
  const lowered = String(value || '').toLowerCase();
  const sanitized = lowered.replace(/[^a-z0-9_-]/g, '-').slice(0, 63);
  return sanitized || 'na';
}

function getPublishTimeIso(cloudEvent) {
  return cloudEvent?.data?.message?.publishTime || cloudEvent?.time || '';
}

function enrichIfNewRun(tasks, cloudEvent) {
  if (!Array.isArray(tasks) || tasks.length === 0) return tasks;
  if (tasks[0]?.run_id) return tasks;

  const publishTimeIso = getPublishTimeIso(cloudEvent);
  const startedAt = new Date(publishTimeIso);
  if (!publishTimeIso || Number.isNaN(startedAt.getTime())) {
    throw new Error(`Invalid publish time for run enrichment: ${publishTimeIso}`);
  }
  const runStartedAtUtc = startedAt.toISOString();

  const projectName = tasks[0]?.project_name || CONFIG.projectName || '';
  const pipelineVersion = tasks[0]?.pipeline_version;
  if (!pipelineVersion) {
    throw new Error('Missing pipeline_version for run enrichment');
  }

  const runId = deriveRunId(projectName, pipelineVersion, runStartedAtUtc);
  return tasks.map(task => ({
    ...task,
    project_name: projectName || undefined,
    run_started_at_utc: runStartedAtUtc,
    run_id: runId,
    step_name: resolveStepName(task),
  }));
}

/**
 * Stable id for the current Pub/Sub payload so redeliveries reuse the same VM name
 * instead of spawning duplicates with Date.now().
 */
function taskKeyFromMessage(message) {
  return crypto.createHash('sha256').update(message).digest('hex').slice(0, 12);
}

/**
 * Converts a Python import path to the absolute yaml path inside the container.
 *
 * Structure of model_class_path:  <package dirs>.<module file>.<ClassName>
 *
 *   e.g. "src.pipelines.logistic_regression.model_service.logistic_regression_service.ModelService"
 *         └──────────────── directory part ───────────────┘  └────── .py file ──────┘  └─ class ─┘
 *
 * The Dockerfile sets WORKDIR /ms, so the absolute path inside the container is:
 *   /ms/<directory part>/grpc_deployment_<env>.yaml
 *
 * Rule: drop the last two segments (class name + .py file name),
 *       join the rest with '/', prepend /ms/, append /grpc_deployment_<env>.yaml.
 *
 * The env value comes from the Pub/Sub message field "env" (set by the scheduler).
 * It must match the filename generated by aigear-deploy-model (helm_chart.py).
 *
 * @param {string} modelClassPath  e.g. "src.pipelines...ModelService"
 * @param {string} env             e.g. "staging" | "production"
 * @returns {string}               e.g. "/ms/src/pipelines/.../grpc_deployment_staging.yaml"
 */
function modelClassPathToYaml(modelClassPath, env) {
  const parts = modelClassPath.split('.');
  const last  = parts[parts.length - 1];
  // parts[-1] = class name (PascalCase)
  // parts[-2] = .py file name (snake_case)
  // Everything before those two segments is the directory path
  const dirParts = /^[A-Z]/.test(last) ? parts.slice(0, -2) : parts;
  return '/ms/' + dirParts.join('/') + `/grpc_deployment_${env}.yaml`;
}

/**
 * Validates required fields on the current task object.
 */
function validateTask(current) {
  if (!current.docker_image) throw new Error('Missing required field: docker_image');
  if (!current.vm_name)      throw new Error('Missing required field: vm_name');
  if (!current.step_name && !current.model_class_path) {
    throw new Error('Task must specify step_name or model_class_path (or both)');
  }
}

/**
 * Builds the pipeline command for the VM startup script.
 *
 * The venv name comes from env.json → pipelines.<name>.venv_pl,
 * and must match the venv directory created in Dockerfile.pl by uv.
 *
 * Base path is fixed to {{VENVBASEDIR}}/ — must stay in sync with:
 *   aigear/common/constant.py  VENV_BASE_DIR = "/opt/venv"
 *
 * The venv name is user-supplied (per pipeline, per Dockerfile), so it is
 * validated to prevent path traversal. Names must be alphanumeric, hyphens,
 * or underscores only.
 *
 * @param {object} current  Task object from the Pub/Sub message
 * @returns {string}        Shell command fragment, or '' when step_name is absent
 */
const SAFE_SHELL_ARG = /^[a-zA-Z0-9_-]+$/;

function assertSafeShellArg(value, label) {
  if (!value || !SAFE_SHELL_ARG.test(value)) {
    throw new Error(
      `Invalid ${label} "${value}": only alphanumerics, hyphens, and underscores are allowed`
    );
  }
}

function buildPipelineCommand(current) {
  if (!current.step_name) return '';

  assertSafeShellArg(current.pipeline_version, 'pipeline_version');
  assertSafeShellArg(current.step_name, 'step_name');

  const baseArgs = `--version ${current.pipeline_version} --step ${current.step_name}`;

  if (!current.venv) {
    return `aigear-task workflow ${baseArgs}`;
  }

  assertSafeShellArg(current.venv, 'venv');

  return `{{VENVBASEDIR}}/${current.venv}/bin/aigear-task workflow ${baseArgs}`;
}

// ─── Startup Script ───────────────────────────────────────────────────────────

/**
 * Builds the VM startup script.
 *
 * Because the custom VM images (ml-training-cpu-image / ml-training-gpu-image)
 * already have Docker, gcloud, and kubectl pre-installed, the script only needs to:
 *   1. Authenticate Docker to Artifact Registry
 *   2. Pull the application image
 *   3. Run the pipeline step (if any)
 *   4. Extract the deployment yaml from the image and run kubectl apply (if any)
 *   5. Publish the next Pub/Sub message and self-delete the VM
 *
 * @param {object} p
 * @param {string} p.dockerImage
 * @param {string} p.gpuFlag          '--gpus all' or ''
 * @param {string} p.pipelineCommand  empty → skip pipeline step
 * @param {string} p.yamlPathInImage  empty → skip deploy step
 * @param {string} p.nextMessage      serialised JSON for the next Pub/Sub message
 * @param {string} p.topicName
 * @param {string} p.gkeCluster   GKE cluster name (from task field gke_cluster)
 * @param {string} p.gkeZone      GKE cluster zone  (from task field gke_zone)
 * @returns {string}
 */
function buildStartupScript({
  dockerImage,
  gpuFlag,
  pipelineCommand,
  yamlPathInImage,
  nextMessage,
  topicName,
  gkeCluster,
  gkeZone,
  runId,
  runStartedAtUtc,
  pipelineVersion,
  projectName,
  stepName,
}) {
  // Single-quote-escape all values interpolated into shell to prevent injection
  const esc = s => s.replace(/'/g, "'\\''");

  // Shared handler: publish terminal error to Pub/Sub and delete this VM.
  const fatalErrorFn = `
# Publish a terminal error and delete this VM (stops the Pub/Sub pipeline).
fatal_error() {
  local exit_code="$1"
  gcloud pubsub topics publish '${esc(topicName)}' --message '{"error":true,"exit_code":"'"$exit_code"'"}' || true
  gcp_zone=$(curl -sf -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/zone | cut -d/ -f4)
  sleep ${CONFIG.vm.sleepBeforeDelete}
  gcloud compute instances delete "$(hostname | cut -d. -f1)" --zone "$gcp_zone" --quiet
  exit 1
}
`;

  // ── Pipeline step ────────────────────────────────────────────────────────────
  // A pipeline failure is fatal: publish an error message, then delete the VM.
  const dockerEnvArgs = [
    `-e AIGEAR_RUN_ID='${esc(runId || '')}'`,
    `-e AIGEAR_RUN_STARTED_AT_UTC='${esc(runStartedAtUtc || '')}'`,
    `-e AIGEAR_PIPELINE_VERSION='${esc(pipelineVersion || '')}'`,
    `-e AIGEAR_STEP_NAME='${esc(stepName || '')}'`,
    `-e AIGEAR_PROJECT_NAME='${esc(projectName || '')}'`,
  ].join(' ');

  const runPipeline = pipelineCommand ? `
# ── Pipeline step ──
docker_exit_code=0
docker run ${gpuFlag} ${dockerEnvArgs} '${esc(dockerImage)}' sh -c '${esc(pipelineCommand)}' || docker_exit_code=$?
if [ "$docker_exit_code" -ne 0 ]; then
  fatal_error pipeline_failed
fi
` : '';

  // ── Deploy step ──────────────────────────────────────────────────────────────
  // A deploy failure is fatal: publish an error message, then delete the VM.
  //
  // gcloud + kubectl are pre-installed in the custom VM image, so no installation needed.
  //
  // Steps:
  //   1. docker create (no run) → docker cp → docker rm   — extract yaml from image
  //   2. gcloud container clusters get-credentials        — fetch GKE kubeconfig
  //      GKE_CLUSTER / GKE_ZONE / GKE_PROJECT must be set as VM metadata or env vars
  //   3. kubectl apply                                     — deploy to the cluster
  //
  // "cmd || step_exit_code=$?" captures a non-zero exit without triggering set -e.
  const runDeploy = yamlPathInImage ? `
# ── Deploy step (VM-native kubectl) ──
# 1. Extract yaml from image without starting a container
create_exit_code=0
DEPLOY_CID=$(docker create '${esc(dockerImage)}') || create_exit_code=$?
if [ "$create_exit_code" -ne 0 ]; then
  fatal_error docker_image_not_found
fi
cp_exit_code=0
docker cp "$DEPLOY_CID":'${esc(yamlPathInImage)}' /tmp/grpc_deployment.yaml || cp_exit_code=$?
docker rm "$DEPLOY_CID" || true
if [ "$cp_exit_code" -ne 0 ]; then
  fatal_error deploy_failed
fi

# 2. Fetch GKE credentials (gcloud pre-installed in the custom VM image)
# gkeCluster / gkeZone are inlined at script-generation time by the Cloud Function
# Explicitly set KUBECONFIG so both gcloud and kubectl use the same file,
# regardless of which user/HOME the metadata script runner uses.
export KUBECONFIG=/tmp/gke-kubeconfig
credentials_exit_code=0
gcloud container clusters get-credentials '${esc(gkeCluster)}' \
  --region '${esc(gkeZone)}' --project '${esc(CONFIG.projectId)}' || credentials_exit_code=$?
if [ "$credentials_exit_code" -ne 0 ]; then
  rm -f /tmp/gke-kubeconfig
  rm -f /tmp/grpc_deployment.yaml
  fatal_error deploy_failed
fi

# 3. Apply (capture exit code without aborting under set -e)
deploy_exit_code=0
kubectl apply -f /tmp/grpc_deployment.yaml --validate=false || deploy_exit_code=$?
rm -f /tmp/gke-kubeconfig
rm -f /tmp/grpc_deployment.yaml

if [ "$deploy_exit_code" -ne 0 ]; then
  fatal_error deploy_failed
fi
` : '';

  const startupMarker = JSON.stringify({
    log_source: 'ml_pipeline',
    event: 'vm_startup',
    run_id: runId,
    run_started_at_utc: runStartedAtUtc,
    pipeline_version: pipelineVersion,
    step_name: stepName,
    project_name: projectName || undefined,
  });

  // gcloud and docker are already on PATH in the custom image — no sudo needed.
  // nextMessage is inlined as a literal string by the Cloud Function at script-generation
  // time — the esc() function ensures single-quote safety for shell injection.
  return `#!/bin/bash
set -euo pipefail
${fatalErrorFn}

echo '${esc(startupMarker)}'

# Authenticate Docker to Artifact Registry (gcloud pre-installed in custom image)
# Extract registry hostname from the image path (e.g. asia-northeast1-docker.pkg.dev)
REGISTRY=$(echo '${esc(dockerImage)}' | cut -d/ -f1)
auth_exit_code=0
gcloud auth configure-docker "$REGISTRY" --quiet || auth_exit_code=$?
if [ "$auth_exit_code" -ne 0 ]; then
  fatal_error registry_auth_failed
fi

pull_exit_code=0
docker pull '${esc(dockerImage)}' || pull_exit_code=$?
if [ "$pull_exit_code" -ne 0 ]; then
  fatal_error docker_image_not_found
fi
${runPipeline}
${runDeploy}
# ── Notify next step and self-delete ──
gcloud pubsub topics publish '${esc(topicName)}' --message '${esc(nextMessage)}'
gcp_zone=$(curl -sf -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/zone | cut -d/ -f4)
sleep ${CONFIG.vm.sleepBeforeDelete}
gcloud compute instances delete "$(hostname | cut -d. -f1)" --zone "$gcp_zone" --quiet
`;
}

// ─── VM Config ────────────────────────────────────────────────────────────────

/**
 * Fetches the runtime service account email from the GCE metadata server.
 * Works for both Cloud Functions and GCE VMs — no hardcoding needed.
 *
 * @returns {Promise<string>}
 */
async function getRuntimeServiceAccount() {
  const url = 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email';
  const res = await fetch(url, { headers: { 'Metadata-Flavor': 'Google' } });
  if (!res.ok) throw new Error(`Failed to fetch service account from metadata server: ${res.status}`);
  return res.text();
}

/**
 * Builds a complete VM config object.
 * Returns a fresh object on every call — no shared mutable state, concurrency-safe.
 * serviceAccount is passed in by the caller (fetched once before the zone-fallback loop).
 *
 * Key points for the custom image:
 *   - sourceImage points to the pre-baked custom image (has Docker/gcloud/kubectl)
 *   - No startup-script installation of tools is needed
 *   - GPU VMs use on_host_maintenance=TERMINATE (required for GPUs)
 *   - CPU VMs use MIGRATE (live-migration friendly)
 */
function buildVmConfig({ current, vmName, startupScript, zone, serviceAccount }) {
  const { projectId, region } = CONFIG;
  // gpu:true/false is the canonical way to request a GPU VM.
  // on_host_maintenance is NOT used for this check: CPU VMs also support TERMINATE,
  // so that field is an unreliable indicator of GPU presence.
  const isGpu = current.gpu === true;

  const machineType = isGpu
    ? (current.spec || CONFIG.vm.defaultGpuSpec)
    : (current.spec || CONFIG.vm.defaultCpuSpec);

  // Use the pre-baked custom image; allow per-task override via vm_image field
  const sourceImage = isGpu
    ? `projects/${projectId}/global/images/${current.vm_image || CONFIG.vm.defaultGpuImage}`
    : `projects/${projectId}/global/images/${current.vm_image || CONFIG.vm.defaultCpuImage}`;

  const rawDisk    = isGpu
    ? (current.disk_size_gb || CONFIG.vm.defaultGpuDisk)
    : (current.disk_size_gb || CONFIG.vm.defaultCpuDisk);
  // Enforce minimum disk size — both custom images are ~50 GB
  const minDisk    = isGpu ? CONFIG.vm.minGpuDisk : CONFIG.vm.minCpuDisk;
  const diskSizeGb = String(Math.max(parseInt(rawDisk, 10) || minDisk, minDisk));

  // gpu_type is per-task so different tasks can use different GPU models
  const gpuType = current.gpu_type || CONFIG.vm.defaultGpuType;
  const guestAccelerators = isGpu ? [{
    acceleratorType:  `projects/${projectId}/zones/${zone}/acceleratorTypes/${gpuType}`,
    acceleratorCount: parseInt(current.gpu_count || String(CONFIG.vm.defaultGpuCount), 10) || CONFIG.vm.defaultGpuCount,
  }] : [];

  return {
    kind:        'compute#instance',
    name:        vmName,
    zone:        `projects/${projectId}/zones/${zone}`,
    machineType: `projects/${projectId}/zones/${zone}/machineTypes/${machineType}`,
    displayDevice: { enableDisplay: false },
    metadata: {
      kind:  'compute#metadata',
      items: [{ key: 'startup-script', value: startupScript }],
    },
    tags:  { items: [] },
    disks: [{
      kind:       'compute#attachedDisk',
      type:       'PERSISTENT',
      boot:       true,
      mode:       'READ_WRITE',
      autoDelete: true,
      deviceName: vmName,
      initializeParams: {
        sourceImage,
        diskType:   `projects/${projectId}/zones/${zone}/diskTypes/pd-standard`,
        diskSizeGb,
      },
      diskEncryptionKey: {},
    }],
    canIpForward: false,
    networkInterfaces: [{
      kind:       'compute#networkInterface',
      subnetwork: `projects/${projectId}/regions/${region}/subnetworks/default`,
      accessConfigs: [{
        kind:        'compute#accessConfig',
        name:        'External NAT',
        type:        'ONE_TO_ONE_NAT',
        networkTier: 'PREMIUM',
      }],
      aliasIpRanges: [],
    }],
    description: '',
    labels:      {
      run_id: sanitizeLabelValue(current.run_id),
      pipeline_version: sanitizeLabelValue(current.pipeline_version),
      step_name: sanitizeLabelValue(resolveStepName(current)),
    },
    scheduling: {
      preemptible:       false,
      // GPU VMs must use TERMINATE (live migration is not supported).
      // CPU VMs default to MIGRATE (live-migration friendly).
      onHostMaintenance: isGpu ? 'TERMINATE' : 'MIGRATE',
      automaticRestart:  true,
      nodeAffinities:    [],
    },
    deletionProtection:  false,
    reservationAffinity: { consumeReservationType: 'ANY_RESERVATION' },
    serviceAccounts: [{
      email:  serviceAccount,
      scopes: ['https://www.googleapis.com/auth/cloud-platform'],
    }],
    shieldedInstanceConfig: {
      enableSecureBoot:          false,
      enableVtpm:                true,
      enableIntegrityMonitoring: true,
    },
    confidentialInstanceConfig: { enableConfidentialCompute: false },
    ...(guestAccelerators.length > 0 ? { guestAccelerators } : {}),
  };
}

// ─── Zone Fallback ────────────────────────────────────────────────────────────

function errorText(err) {
  return typeof err === 'string' ? err : JSON.stringify(err.message || err);
}

function isExhaustedError(err) {
  return EXHAUSTED_CODES.some(code => errorText(err).includes(code));
}

function isAlreadyExistsError(err) {
  const msg = errorText(err);
  return msg.includes('alreadyExists') || msg.includes('ALREADY_EXISTS');
}

function isNotFoundError(err) {
  const status = err?.response?.status ?? err?.code;
  if (status === 404) return true;
  const reasons = err?.response?.data?.error?.errors;
  if (Array.isArray(reasons) && reasons.some(e => e.reason === 'notFound')) {
    return true;
  }
  const msg = errorText(err);
  return /not.?found/i.test(msg) || msg.includes('NOT_FOUND');
}

async function vmExistsInZone(compute, zone, vmName) {
  try {
    await compute.instances.get({
      project:  CONFIG.projectId,
      zone,
      instance: vmName,
    });
    return true;
  } catch (err) {
    if (isNotFoundError(err)) return false;
    throw err;
  }
}

/**
 * Return the zone where a VM with this name already exists, or null.
 */
async function findExistingVmZone(compute, vmName) {
  for (const z of CONFIG.fallbackZones) {
    if (await vmExistsInZone(compute, z, vmName)) return z;
  }
  return null;
}

/**
 * True when VM creation cannot succeed by switching zones or redelivering Pub/Sub
 * (e.g. custom source image missing from the project).
 */
function isFatalVmError(err) {
  const msg = errorText(err);
  if (isExhaustedError(err)) return false;
  if (/global\/images\//i.test(msg) && /not found|NOT_FOUND/i.test(msg)) return true;
  if (!FATAL_VM_ERROR_CODES.some(code => msg.includes(code))) return false;
  // NOT_FOUND for a zone-local accelerator is zone exhaustion, not fatal
  if (msg.includes('acceleratorTypes')) return false;
  return /global\/images\//i.test(msg) || /sourceImage|source image/i.test(msg);
}

/**
 * Publish a terminal error so the pipeline stops and Pub/Sub does not keep
 * redelivering the same task queue (matches startup-script error handling).
 */
async function publishPipelineError(authClient, err, forcedExitCode = null) {
  const exitCode = forcedExitCode || (isFatalVmError(err) ? 'vm_image_not_found' : 'vm_create_failed');
  const payload = JSON.stringify({
    error:     true,
    exit_code: exitCode,
    detail:    String(err?.message || err).slice(0, 500),
  });

  try {
    const pubsub = google.pubsub({ version: 'v1', auth: authClient });
    await pubsub.projects.topics.publish({
      topic: `projects/${CONFIG.projectId}/topics/${CONFIG.topicName}`,
      requestBody: {
        messages: [{ data: Buffer.from(payload).toString('base64') }],
      },
    });
    console.log(`Published pipeline error to ${CONFIG.topicName}: ${payload}`);
  } catch (pubErr) {
    console.error(`Failed to publish pipeline error: ${pubErr.message}`);
  }
}

async function failTaskAndStop(getAuthClient, err, forcedExitCode, logFn) {
  await logFn();
  const authClient = await getAuthClient();
  await publishPipelineError(authClient, err, forcedExitCode);
}

/**
 * Poll the insert operation briefly so fatal errors (e.g. missing custom image)
 * are caught before acking Pub/Sub, without waiting for the full VM boot.
 *
 * @returns {{ exhausted?: true, pending?: true }}
 */
async function waitForInsertOperation(compute, zone, operation) {
  const deadline = Date.now() + INSERT_OPERATION_WAIT_MS;

  while (operation.status !== 'DONE' && Date.now() < deadline) {
    await sleep(2000);
    const opRes = await compute.zoneOperations.get({
      project:   CONFIG.projectId,
      zone,
      operation: operation.name,
    });
    operation = opRes.data;
  }

  if (operation.status === 'DONE' && operation.error) {
    const opErr = JSON.stringify(operation.error);
    if (isFatalVmError(opErr)) {
      throw new Error(`Fatal VM error (non-retryable): ${opErr}`);
    }
    if (isExhaustedError(opErr)) {
      return { exhausted: true };
    }
    throw new Error(`VM operation failed: ${opErr}`);
  }

  if (operation.status !== 'DONE') {
    console.log(`VM insert still running in zone ${zone}, acking Pub/Sub early.`);
    return { pending: true };
  }

  return {};
}

/**
 * Tries each fallback zone in order until the VM is created.
 * serviceAccount is resolved once before the loop to avoid redundant metadata calls.
 *
 * @param {object}   authClient
 * @param {Function} configFactory  (zone, serviceAccount) => vmConfig
 * @param {string}   vmName
 * @returns {Promise<{zone: string, operationPending: boolean, deduplicated: boolean}>}
 */
async function createVMWithFallback(authClient, configFactory, vmName, logContext = {}) {
  const compute        = google.compute({ version: 'v1', auth: authClient });
  const serviceAccount = await getRuntimeServiceAccount();
  await writeCloudFunctionLog({
    event: 'runtime_service_account_resolved',
    message: 'runtime_service_account_resolved',
    task: logContext,
    extra: { service_account_email: serviceAccount },
  });

  const existingZone = await findExistingVmZone(compute, vmName);
  if (existingZone) {
    await writeCloudFunctionLog({
      event: 'vm_already_exists',
      message: 'vm_already_exists',
      task: { ...logContext, zone: existingZone, instance_name: vmName },
      extra: {
        operation_pending: false,
        deduplicated: true,
      },
    });
    console.log(`VM ${vmName} already exists in zone ${existingZone}, skipping insert.`);
    return { zone: existingZone, operationPending: false, deduplicated: true };
  }

  for (const z of CONFIG.fallbackZones) {
    await writeCloudFunctionLog({
      event: 'vm_zone_attempt',
      message: 'vm_zone_attempt',
      task: { ...logContext, zone: z },
    });
    const zonedConfig = configFactory(z, serviceAccount);

    try {
      const response = await compute.instances.insert({
        project:     CONFIG.projectId,
        zone:        z,
        requestBody: zonedConfig,
      });

      const operation = response.data;
      await writeCloudFunctionLog({
        event: 'vm_creation_started',
        message: 'vm_creation_started',
        task: { ...logContext, zone: z, instance_name: zonedConfig.name },
        extra: { operation_name: operation.name },
      });

      const waitResult = await waitForInsertOperation(compute, z, operation);
      if (waitResult.exhausted) {
        await writeCloudFunctionLog({
          event: 'vm_zone_exhausted',
          message: 'vm_zone_exhausted',
          severity: 'WARNING',
          task: { ...logContext, zone: z },
        });
        continue;
      }

      await writeCloudFunctionLog({
        event: 'vm_created_in_zone',
        message: 'vm_created_in_zone',
        task: { ...logContext, zone: z, instance_name: zonedConfig.name },
        extra: {
          operation_pending: Boolean(waitResult.pending),
          deduplicated: false,
        },
      });

      console.log(
        `VM creation ${waitResult.pending ? 'started' : 'completed'} in zone ${z}, name: ${zonedConfig.name}, operation: ${operation.name}`,
      );
      return { zone: z, operationPending: Boolean(waitResult.pending), deduplicated: false };

    } catch (err) {
      if (isAlreadyExistsError(err)) {
        await writeCloudFunctionLog({
          event: 'vm_already_exists',
          message: 'vm_already_exists',
          task: { ...logContext, zone: z, instance_name: zonedConfig.name },
          extra: {
            operation_pending: false,
            deduplicated: true,
          },
        });
        console.log(`VM ${zonedConfig.name} already exists in zone ${z}, treating as success.`);
        return { zone: z, operationPending: false, deduplicated: true };
      }
      if (isFatalVmError(err)) {
        throw err;
      }
      if (isExhaustedError(err)) {
        await writeCloudFunctionLog({
          event: 'vm_zone_exhausted',
          message: 'vm_zone_exhausted',
          severity: 'WARNING',
          task: { ...logContext, zone: z },
          extra: { exhausted_error: err.message || String(err) },
        });
        continue;
      }
      throw err;
    }
  }

  throw new Error('All zones exhausted, no resources available.');
}

// ─── Main ─────────────────────────────────────────────────────────────────────

functions.cloudEvent('cronjobProcessPubSub', async cloudEvent => {
  const message = Buffer.from(cloudEvent.data.message.data, 'base64').toString().trim();
  await writeCloudFunctionLog({
    event: 'pubsub_message_received',
    message: 'pubsub_message_received',
    extra: { raw_message: message },
  });
  let authClient;
  const getAuthClient = async () => {
    if (authClient) return authClient;
    const auth = new google.auth.GoogleAuth({
      scopes: ['https://www.googleapis.com/auth/cloud-platform'],
    });
    authClient = await auth.getClient();
    return authClient;
  };

  // Terminal message checks
  if (message.startsWith('Exit code:')) {
    await writeCloudFunctionLog({
      event: 'pipeline_failed_terminal_message',
      message: 'pipeline_failed_terminal_message',
      severity: 'ERROR',
      extra: { raw_message: message },
    });
    return;
  }
  if (message === MSG.PIPELINE_DONE || message === MSG.EMPTY_QUEUE) {
    await writeCloudFunctionLog({
      event: 'pipeline_completed',
      message: 'pipeline_completed',
    });
    return;
  }

  // Parse JSON
  let cronjobInfo;
  try {
    cronjobInfo = JSON.parse(message);
  } catch (err) {
    await writeCloudFunctionLog({
      event: 'invalid_json_message',
      message: 'invalid_json_message',
      severity: 'ERROR',
      extra: { raw_message: message },
    });
    try {
      const client = await getAuthClient();
      await publishPipelineError(client, err, 'task_invalid');
    } catch (publishErr) {
      console.error(`Failed to publish validation error: ${publishErr.message}`);
    }
    return;
  }

  // Error payload (from VM startup script or Cloud Function after VM create failure)
  if (cronjobInfo?.error) {
    await writeCloudFunctionLog({
      event: 'pipeline_step_failed',
      message: 'pipeline_step_failed',
      severity: 'ERROR',
      extra: {
        exit_code: cronjobInfo.exit_code || undefined,
        detail: cronjobInfo.detail || undefined,
      },
    });
    const detail = cronjobInfo.detail ? `, detail: ${cronjobInfo.detail}` : '';
    console.error(`Pipeline step failed with exit code: ${cronjobInfo.exit_code}${detail}`);
    return;
  }

  if (!Array.isArray(cronjobInfo) || cronjobInfo.length === 0) {
    try {
      await failTaskAndStop(getAuthClient, new Error('empty_or_invalid_cronjob_info'), 'task_invalid', async () => {
        await writeCloudFunctionLog({
          event: 'empty_or_invalid_cronjob_info',
          message: 'empty_or_invalid_cronjob_info',
          severity: 'WARNING',
        });
      });
    } catch (publishErr) {
      console.error(`Failed to publish validation error: ${publishErr.message}`);
    }
    return;
  }

  try {
    cronjobInfo = enrichIfNewRun(cronjobInfo, cloudEvent);
  } catch (err) {
    try {
      await failTaskAndStop(getAuthClient, err, 'task_invalid', async () => {
        await writeCloudFunctionLog({
          event: 'run_enrichment_failed',
          message: 'run_enrichment_failed',
          severity: 'ERROR',
          extra: { error: err.message },
        });
      });
    } catch (publishErr) {
      console.error(`Failed to publish enrichment error: ${publishErr.message}`);
    }
    return;
  }

  const [current, ...remaining] = cronjobInfo;
  await writeCloudFunctionLog({
    event: 'run_context_initialized',
    message: 'run_context_initialized',
    task: current,
  });
  await writeCloudFunctionLog({
    event: 'task_processing_started',
    message: 'task_processing_started',
    task: current,
  });

  // Validate
  try {
    validateTask(current);
  } catch (err) {
    await writeCloudFunctionLog({
      event: 'task_validation_failed',
      message: 'task_validation_failed',
      severity: 'ERROR',
      task: current,
      extra: { error: err.message },
    });
    try {
      const client = await getAuthClient();
      await publishPipelineError(client, err, 'task_invalid');
    } catch (publishErr) {
      console.error(`Failed to publish validation error: ${publishErr.message}`);
    }
    return;
  }

  // Build commands
  // Use current.gpu (boolean) to determine GPU mode — not on_host_maintenance.
  const isGpu   = current.gpu === true;
  const gpuFlag = isGpu ? '--gpus all' : '';

  let pipelineCommand;
  try {
    pipelineCommand = buildPipelineCommand(current);
  } catch (err) {
    await writeCloudFunctionLog({
      event: 'pipeline_command_build_failed',
      message: 'pipeline_command_build_failed',
      severity: 'ERROR',
      task: current,
      extra: { error: err.message },
    });
    try {
      const client = await getAuthClient();
      await publishPipelineError(client, err, 'task_invalid');
    } catch (publishErr) {
      console.error(`Failed to publish validation error: ${publishErr.message}`);
    }
    return;
  }

  // Convert model_class_path to the yaml path inside the container.
  // current.env is set by the scheduler (e.g. "staging" / "production") and
  // must match the filename produced by aigear-deploy-model (helm_chart.py).
  const yamlPathInImage = current.model_class_path
    ? modelClassPathToYaml(current.model_class_path, current.env || 'staging')
    : '';

  await writeCloudFunctionLog({
    event: 'task_command_metadata',
    message: 'task_command_metadata',
    task: current,
    extra: {
      pipeline_command_present: Boolean(pipelineCommand),
      yaml_path_in_image_present: Boolean(yamlPathInImage),
    },
  });

  // Build startup script
  const nextMessage   = JSON.stringify(remaining);
  const startupScript = buildStartupScript({
    dockerImage:    current.docker_image,
    gpuFlag,
    pipelineCommand,
    yamlPathInImage,
    nextMessage,
    topicName:      CONFIG.topicName,
    gkeCluster:     current.gke_cluster || '',
    gkeZone:        current.gke_zone    || '',
    runId:          current.run_id || '',
    runStartedAtUtc: current.run_started_at_utc || '',
    pipelineVersion: current.pipeline_version || '',
    projectName:     current.project_name || '',
    stepName:        resolveStepName(current),
  });

  const vmName = `${current.vm_name}-${taskKeyFromMessage(message)}`;

  // Create VM
  try {
    authClient = await getAuthClient();

    const vmCreation = await createVMWithFallback(
      authClient,
      (zone, serviceAccount) => buildVmConfig({ current, vmName, startupScript, zone, serviceAccount }),
      vmName,
      {
        run_id: current.run_id,
        run_started_at_utc: current.run_started_at_utc,
        pipeline_version: current.pipeline_version,
        step_name: resolveStepName(current),
        project_name: current.project_name || undefined,
        instance_name: vmName,
      },
    );

    await writeCloudFunctionLog({
      event: 'vm_created',
      message: 'vm_created',
      task: { ...current, instance_name: vmName, zone: vmCreation.zone },
      extra: {
        operation_pending: vmCreation.operationPending,
        deduplicated: vmCreation.deduplicated,
      },
    });

  } catch (err) {
    await writeCloudFunctionLog({
      event: 'vm_creation_failed',
      message: 'vm_creation_failed',
      severity: 'ERROR',
      task: { ...current, instance_name: vmName },
      extra: { error: err.message || String(err) },
    });
    if (authClient) {
      await publishPipelineError(authClient, err);
    }
  }
});
