/**
 * Triggered from a message on a Cloud Pub/Sub topic.
 *
 * @param {!Object} event Event payload.
 * @param {!Object} context Metadata for the event.
 */
const Buffer = require('safe-buffer').Buffer;
const Compute = require('@google-cloud/compute');
const compute = new Compute();
// Change this const value to your project
const projectId = 'PROJECTID';
const zone = 'ZONE';

//Build environment and clean up
const commonCommand =
  'cd /var\n' +
  'gcloud auth configure-docker REGION-docker.pkg.dev --quiet\n' +
  'sudo docker pull ${dockerImage}\n' +
  'sudo docker run --gpus all ${dockerImage} ${pipelineCommand}\n' +
  'docker_exit_code=$?\n' +
  "[ ${docker_exit_code} -eq \"0\" ] && gcloud pubsub topics publish TOPICSNAME --message 'createVMDate' || gcloud pubsub topics publish TOPICSNAME --message \"Exit code: ${docker_exit_code}\"\n" +
  'gcp_zone=$(curl -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/instance/zone -s | cut -d/ -f4)\n' +
  'sleep 300\n' +
  'hostname_result=$(hostname)\n' +
  'extracted_name=$(echo ${hostname_result} | cut -d. -f1)\n' +
  'gcloud compute instances delete ${extracted_name} --zone ${gcp_zone}';

const vmConfig = {
  kind: 'compute#instance',
  name: 'cronjob-process-vm',
  zone: `projects/${projectId}/zones/${zone}`,
  machineType: `projects/${projectId}/zones/${zone}/machineTypes/`,
  displayDevice: {
    enableDisplay: false
  },
  metadata: {
    kind: 'compute#metadata',
    items: [
      {
        key: 'startup-script',
        value: commonCommand
      }
    ]
  },
  tags: {
    items: []
  },
  disks: [
    {
      kind: 'compute#attachedDisk',
      type: 'PERSISTENT',
      boot: true,
      mode: 'READ_WRITE',
      autoDelete: true,
      deviceName: 'cronjob-process-vm',
      initializeParams: {
        sourceImage: `projects/cos-cloud/global/images/family/cos-stable`,
        diskType: `projects/${projectId}/zones/${zone}/diskTypes/pd-standard`,
        diskSizeGb: '20'
      },
      diskEncryptionKey: {}
    }
  ],
  canIpForward: false,
  networkInterfaces: [
    {
      kind: 'compute#networkInterface',
      subnetwork: `projects/${projectId}/regions/REGION/subnetworks/default`,
      accessConfigs: [
        {
          kind: 'compute#accessConfig',
          name: 'External NAT',
          type: 'ONE_TO_ONE_NAT',
          networkTier: 'PREMIUM'
        }
      ],
      aliasIpRanges: []
    }
  ],
  description: '',
  labels: {},
  scheduling: {
    preemptible: false,
    onHostMaintenance: 'MIGRATE',
    automaticRestart: true,
    nodeAffinities: []
  },
  deletionProtection: false,
  reservationAffinity: {
    consumeReservationType: 'ANY_RESERVATION'
  },
  serviceAccounts: [
    {
      email: `SERVICEACCOUNT`,
      scopes: [
        'https://www.googleapis.com/auth/cloud-platform'
      ]
    }
  ],
  shieldedInstanceConfig: {
    enableSecureBoot: false,
    enableVtpm: true,
    enableIntegrityMonitoring: true
  },
  confidentialInstanceConfig: {
    enableConfidentialCompute: false
  }
}
const functions = require('@google-cloud/functions-framework');

// Register a CloudEvent callback with the Functions Framework that will
// be executed when the Pub/Sub trigger topic receives a message.
functions.cloudEvent('cronjobProcessPubSub', cloudEvent => {
  // The Pub/Sub message is passed as the CloudEvent's data payload.
  const message = Buffer.from(cloudEvent.data.message.data, 'base64').toString();
  const cronjobInfo = JSON.parse(message);
  if(cronjobInfo.length == 0) {
    return;
  }
  console.log(`vmName is ${cronjobInfo[0].vm_name}`);
  console.log(`cronjobInfo.spec is ${cronjobInfo[0].spec}`);

  let updateCommand = commonCommand
  const isGpu = cronjobInfo[0].on_host_maintenance === 'TERMINATE';
  console.log(`onHostMaintenance is ${cronjobInfo[0].on_host_maintenance}`);
  if (isGpu) {
    const spec = (cronjobInfo[0].spec ? String(cronjobInfo[0].spec) : 'n1-standard-4');
    vmConfig.machineType = `projects/${projectId}/zones/${zone}/machineTypes/` + spec;
    console.log(`GPU mode detected: e2-* not supported, fallback machine type -> ${spec}`);

    // T4
    const T4_ACCELERATOR = `projects/${projectId}/zones/${zone}/acceleratorTypes/nvidia-tesla-t4`;
    const gpuCount = parseInt(cronjobInfo[0].gpu_count || '1', 10) || 1;
    vmConfig.guestAccelerators = [
      { acceleratorType: T4_ACCELERATOR, acceleratorCount: gpuCount }
    ];

    // vm image
    const vmImage = cronjobInfo[0].vm_image ? cronjobInfo[0].vm_image : 'ml-training-gpu-image';
    vmConfig.disks[0].initializeParams.sourceImage = `projects/${projectId}/global/images/` + vmImage;

    // VM and hard disk name
    vmConfig.name = cronjobInfo[0].vm_name;
    vmConfig.disks[0].deviceName = cronjobInfo[0].vm_name;
    const diskSizeGb = cronjobInfo[0].disk_size_gb ? cronjobInfo[0].disk_size_gb : '50';
    vmConfig.disks[0].initializeParams.diskSizeGb = String(Math.max(parseInt(diskSizeGb, 10) || 50, 50));
  } else {
    // set vm spec
    const spec = cronjobInfo[0].spec ? cronjobInfo[0].spec : 'e2-medium';
    vmConfig.machineType = `projects/${projectId}/zones/${zone}/machineTypes/` + spec;

    // Not T4
    vmConfig.guestAccelerators = [];

    // set vm image
    const vmImage = cronjobInfo[0].vm_image ? cronjobInfo[0].vm_image : 'ml-model-training-cloud-function-image';
    vmConfig.disks[0].initializeParams.sourceImage = `projects/${projectId}/global/images/` + vmImage;

    // VM and hard disk name
    vmConfig.name = cronjobInfo[0].vm_name;
    vmConfig.disks[0].deviceName = cronjobInfo[0].vm_name;
    const diskSizeGb = cronjobInfo[0].disk_size_gb ? cronjobInfo[0].disk_size_gb : '20';
    vmConfig.disks[0].initializeParams.diskSizeGb = diskSizeGb;

    // cpu rm gpu command
    updateCommand = updateCommand.replace('--gpus all ', '');
  }
  vmConfig.scheduling.onHostMaintenance = cronjobInfo[0].on_host_maintenance;
  const vmName = cronjobInfo[0].vm_name + Date.now();
  const dockerImage = cronjobInfo[0].docker_image;
  // pipeline step in commonCommand
  const pipelineCommand = 'aigear-workflow --version ' + cronjobInfo[0].pipeline_version + ' --step ' + cronjobInfo[0].pipeline_step

  console.log(JSON.stringify(cronjobInfo));
  cronjobInfo.shift()

  vmConfig.metadata.items[0].value = updateCommand.replace(/\${dockerImage}/g,dockerImage).replace(/\${pipelineCommand}/g,pipelineCommand).replace('createVMDate',JSON.stringify(cronjobInfo));

  try {
    compute.zone(zone)
      .createVM(vmName, vmConfig)
      .then(data => {
        // Operation pending.
        const vm = data[0];
        const operation = data[1];
        console.log(`VM being created: ${vm.id}`);
        console.log(`Operation info: ${operation.id}`);
        return operation.promise();
      })
      .then(() => {
        const message = 'VM created with success, Cloud Function finished execution.';
        console.log(message);
      })
      .catch(err => {
        console.log(err);
      });

  } catch (err) {
    console.log(err);
  }
});
