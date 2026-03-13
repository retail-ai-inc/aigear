const Buffer = require('safe-buffer').Buffer;
const { google } = require('googleapis');
const functions = require('@google-cloud/functions-framework');

// ─── Config ───────────────────────────────────────────────────────────────────

const CONFIG = {
  projectId: 'PROJECTID',
  region:    'REGION',
  topicName: 'TOPICSNAME',
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

// ─── Utilities ────────────────────────────────────────────────────────────────

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/**
 * Converts a Python import path to the absolute yaml path inside the container.
 *
 * Structure of model_class_path:  <package dirs>.<module file>.<ClassName>
 *
 *   e.g. "src.pipelines.logistic_regression.model_service.logistic_regression_service.ModelService"
 *         └──────────────── directory part ───────────────┘  └────── .py file ──────┘  └─ class ─┘
 *
 * The Dockerfile sets WORKDIR /ms, so the absolute path inside the container is:
 *   /ms/<directory part>/grpc_deployment.yaml
 *
 * Rule: drop the last two segments (class name + .py file name),
 *       join the rest with '/', prepend /ms/, append /grpc_deployment.yaml.
 *
 * @param {string} modelClassPath  e.g. "src.pipelines...ModelService"
 * @returns {string}               e.g. "/ms/src/pipelines/.../grpc_deployment.yaml"
 */
function modelClassPathToYaml(modelClassPath) {
  const parts = modelClassPath.split('.');
  const last  = parts[parts.length - 1];
  // parts[-1] = class name (PascalCase)
  // parts[-2] = .py file name (snake_case)
  // Everything before those two segments is the directory path
  const dirParts = /^[A-Z]/.test(last) ? parts.slice(0, -2) : parts;
  return '/ms/' + dirParts.join('/') + '/grpc_deployment.yaml';
}

/**
 * Validates required fields on the current task object.
 */
function validateTask(current) {
  if (!current.docker_image) throw new Error('Missing required field: docker_image');
  if (!current.vm_name)      throw new Error('Missing required field: vm_name');
  if (!current.pipeline_step && !current.model_class_path) {
    throw new Error('Task must specify pipeline_step or model_class_path (or both)');
  }
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
function buildStartupScript({ dockerImage, gpuFlag, pipelineCommand, yamlPathInImage, nextMessage, topicName, gkeCluster, gkeZone }) {
  // Single-quote-escape all values interpolated into shell to prevent injection
  const esc = s => s.replace(/'/g, "'\\''");

  // ── Pipeline step ────────────────────────────────────────────────────────────
  // A pipeline failure is fatal: publish an error message, then delete the VM.
  const runPipeline = pipelineCommand ? `
# ── Pipeline step ──
docker run ${gpuFlag} '${esc(dockerImage)}' ${pipelineCommand}
docker_exit_code=$?
if [ "$docker_exit_code" -ne 0 ]; then
  gcloud pubsub topics publish '${esc(topicName)}' --message '{"error":true,"exit_code":"'"$docker_exit_code"'"}'
  gcp_zone=$(curl -sf -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/zone | cut -d/ -f4)
  sleep 10
  gcloud compute instances delete "$(hostname | cut -d. -f1)" --zone "$gcp_zone" --quiet
  exit 1
fi
` : '';

  // ── Deploy step ──────────────────────────────────────────────────────────────
  // A deploy failure is non-fatal: log it and continue the pipeline.
  //
  // gcloud + kubectl are pre-installed in the custom VM image, so no installation needed.
  //
  // Steps:
  //   1. docker create (no run) → docker cp → docker rm   — extract yaml from image
  //   2. gcloud container clusters get-credentials        — fetch GKE kubeconfig
  //      GKE_CLUSTER / GKE_ZONE / GKE_PROJECT must be set as VM metadata or env vars
  //   3. kubectl apply                                     — deploy to the cluster
  //
  // "cmd || deploy_exit_code=$?" captures a non-zero exit without triggering set -e.
  const runDeploy = yamlPathInImage ? `
# ── Deploy step (VM-native kubectl) ──
# 1. Extract yaml from image without starting a container
DEPLOY_CID=$(docker create '${esc(dockerImage)}')
docker cp "$DEPLOY_CID":'${esc(yamlPathInImage)}' /tmp/grpc_deployment.yaml
docker rm "$DEPLOY_CID"

# 2. Fetch GKE credentials (gcloud pre-installed in the custom VM image)
# gkeCluster / gkeZone are inlined at script-generation time by the Cloud Function
# Explicitly set KUBECONFIG so both gcloud and kubectl use the same file,
# regardless of which user/HOME the metadata script runner uses.
export KUBECONFIG=/tmp/gke-kubeconfig
gcloud container clusters get-credentials '${esc(gkeCluster)}' \
  --region '${esc(gkeZone)}' --project '${esc(CONFIG.projectId)}'

# 3. Apply (capture exit code without aborting under set -e)
deploy_exit_code=0
kubectl apply -f /tmp/grpc_deployment.yaml --validate=false || deploy_exit_code=$?
rm -f /tmp/gke-kubeconfig
rm -f /tmp/grpc_deployment.yaml

if [ "$deploy_exit_code" -ne 0 ]; then
  echo "Model deployment failed with exit code $deploy_exit_code"
else
  echo "Model deployed successfully"
fi
` : '';

  // gcloud and docker are already on PATH in the custom image — no sudo needed.
  // nextMessage is inlined as a literal string by the Cloud Function at script-generation
  // time — the esc() function ensures single-quote safety for shell injection.
  return `#!/bin/bash
set -euo pipefail

# Authenticate Docker to Artifact Registry (gcloud pre-installed in custom image)
gcloud auth configure-docker asia-northeast1-docker.pkg.dev --quiet

docker pull '${esc(dockerImage)}'
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
    labels:      {},
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

function isExhaustedError(err) {
  const msg = typeof err === 'string' ? err : JSON.stringify(err.message || err);
  return EXHAUSTED_CODES.some(code => msg.includes(code));
}

/**
 * Tries each fallback zone in order until the VM is created.
 * serviceAccount is resolved once before the loop to avoid redundant metadata calls.
 *
 * @param {object}   authClient
 * @param {Function} configFactory  (zone, serviceAccount) => vmConfig
 * @returns {Promise<string>}  the zone where the VM was successfully created
 */
async function createVMWithFallback(authClient, configFactory) {
  const compute        = google.compute({ version: 'v1', auth: authClient });
  const serviceAccount = await getRuntimeServiceAccount();
  console.log(`Using service account: ${serviceAccount}`);

  for (const z of CONFIG.fallbackZones) {
    console.log(`Trying zone: ${z}`);
    const zonedConfig = configFactory(z, serviceAccount);

    try {
      const response = await compute.instances.insert({
        project:     CONFIG.projectId,
        zone:        z,
        requestBody: zonedConfig,
      });

      let operation = response.data;
      console.log(`VM creation started in zone ${z}, operation: ${operation.name}`);

      while (operation.status !== 'DONE') {
        await sleep(3000);
        const opRes = await compute.zoneOperations.get({
          project:   CONFIG.projectId,
          zone:      z,
          operation: operation.name,
        });
        operation = opRes.data;
        console.log(`Operation status: ${operation.status}`);
      }

      if (operation.error) {
        const code = operation.error.errors?.[0]?.code || '';
        if (EXHAUSTED_CODES.some(c => code.includes(c))) {
          console.warn(`Zone ${z} exhausted (operation error), trying next...`);
          continue;
        }
        throw new Error(`VM operation failed: ${JSON.stringify(operation.error)}`);
      }

      console.log(`VM created in zone: ${z}, name: ${zonedConfig.name}`);
      return z;

    } catch (err) {
      if (isExhaustedError(err)) {
        console.warn(`Zone ${z} exhausted, trying next...`);
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
  console.log(`Received message: ${message}`);

  // Terminal message checks
  if (message.startsWith('Exit code:')) {
    console.error(`Pipeline failed: ${message}`);
    return;
  }
  if (message === MSG.PIPELINE_DONE || message === MSG.EMPTY_QUEUE) {
    console.log('Pipeline completed, no more steps.');
    return;
  }

  // Parse JSON
  let cronjobInfo;
  try {
    cronjobInfo = JSON.parse(message);
  } catch {
    console.error(`Invalid JSON message: ${message}`);
    return;
  }

  // Error payload
  if (cronjobInfo?.error) {
    console.error(`Pipeline step failed with exit code: ${cronjobInfo.exit_code}`);
    return;
  }

  if (!Array.isArray(cronjobInfo) || cronjobInfo.length === 0) {
    console.log('Empty or invalid cronjobInfo, exiting.');
    return;
  }

  const [current, ...remaining] = cronjobInfo;
  console.log(`Processing task: ${JSON.stringify(current)}`);

  // Validate
  try {
    validateTask(current);
  } catch (err) {
    console.error(`Task validation failed: ${err.message}`);
    return;
  }

  // Build commands
  // Use current.gpu (boolean) to determine GPU mode — not on_host_maintenance.
  const isGpu   = current.gpu === true;
  const gpuFlag = isGpu ? '--gpus all' : '';

  const pipelineCommand = current.pipeline_step
    ? `aigear-workflow --version ${current.pipeline_version} --step ${current.pipeline_step}`
    : '';

  // Convert model_class_path to the yaml path inside the container
  const yamlPathInImage = current.model_class_path
    ? modelClassPathToYaml(current.model_class_path)
    : '';

  console.log(`pipelineCommand: ${pipelineCommand || '(skipped)'}`);
  console.log(`yamlPathInImage: ${yamlPathInImage || '(skipped)'}`);

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
  });

  const vmName = `${current.vm_name}-${Date.now()}`;

  // Create VM
  try {
    const auth = new google.auth.GoogleAuth({
      scopes: ['https://www.googleapis.com/auth/cloud-platform'],
    });
    const authClient = await auth.getClient();

    await createVMWithFallback(
      authClient,
      (zone, serviceAccount) => buildVmConfig({ current, vmName, startupScript, zone, serviceAccount }),
    );

  } catch (err) {
    console.error('Failed to create VM in all zones:', err);
  }
});