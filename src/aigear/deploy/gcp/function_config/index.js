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
const projectId = 'ssc-ape-staging';
const zone = 'asia-northeast1-a';

//Build environment and clean up
const commonCommand = 'cd /var\ngcloud auth configure-docker asia-northeast1-docker.pkg.dev --quiet\nsudo docker pull ${dockerImage}\nsudo docker run ${dockerImage} ${pipelineCommand}\ndocker_exit_code=$?\n[ ${docker_exit_code} -eq "0" ] && gcloud pubsub topics publish medovik-pipelines-pubsub --message \'createVMDate\' || gcloud pubsub topics publish medovik-pipelines-pubsub --message "Exit code: ${docker_exit_code}"\ngcp_zone=$(curl -H Metadata-Flavor:Google http://metadata.google.internal/computeMetadata/v1/instance/zone -s | cut -d/ -f4)\nsleep 300\nhostname_result=$(hostname)\nextracted_name=$(echo ${hostname_result} | cut -d. -f1)\ngcloud compute instances delete ${extracted_name} --zone ${gcp_zone}';

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
        sourceImage: `projects/${projectId}/global/images/ml-model-training-cloud-function-image`,
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
      subnetwork: `projects/${projectId}/regions/asia-northeast1/subnetworks/default`,
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
      email: `ml-model-training@ssc-ape-staging.iam.gserviceaccount.com`,
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
  console.log(`vmName is ${cronjobInfo[0].vmName}`);
  console.log(`command is ${cronjobInfo[0].command}`);
  console.log(`cronjobInfo.spec is ${cronjobInfo[0].spec}`);

  // set vm spec
  const spec = cronjobInfo[0].spec ? cronjobInfo[0].spec : 'e2-medium';
  const machineTypeSpec = `projects/${projectId}/zones/${zone}/machineTypes/` + spec;
  console.log(`machineTypeSpec is ${machineTypeSpec}`);

  vmConfig.machineType = machineTypeSpec;

  // VM and hard disk name
  vmConfig.name = cronjobInfo[0].vmName;
  vmConfig.disks[0].deviceName = cronjobInfo[0].vmName;
  const diskSizeGb = cronjobInfo[0].diskSizeGb ? cronjobInfo[0].diskSizeGb : '20';
  vmConfig.disks[0].initializeParams.diskSizeGb = diskSizeGb;
  vmConfig.scheduling.onHostMaintenance = cronjobInfo[0].onHostMaintenance;
  const vmName = cronjobInfo[0].vmName + Date.now();
  const dockerImage = cronjobInfo[0].dockerImage;

  // pipeline step in commonCommand
  const pipelineCommand = '/opt/venv/medovik/bin/python /medovik/main.py --version ' + cronjobInfo[0].pipelineVersion + ' --step ' + cronjobInfo[0].pipelineStep

  console.log(JSON.stringify(cronjobInfo));
  cronjobInfo.shift()

  vmConfig.metadata.items[0].value = commonCommand.replace(/\${dockerImage}/g,dockerImage).replace(/\${pipelineCommand}/g,pipelineCommand).replace('createVMDate',JSON.stringify(cronjobInfo));
  
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
