import time

from google.cloud import compute_v1
from google.api_core import exceptions
from google.cloud.compute_v1.types import (
    AcceleratorConfig,
    Metadata,
    GetSerialPortOutputInstanceRequest,
    AttachedDisk,
    AttachedDiskInitializeParams,
    NetworkInterface,
    AccessConfig,
    Scheduling,
    Instance,
    Image,
    Items,
)
from google.api_core.extended_operation import ExtendedOperation
from google.api_core.exceptions import NotFound, Conflict
from aigear.common.logger import Logging


logger = Logging(log_name=__name__).console_logging()


STARTUP_SCRIPT = r"""#!/usr/bin/env bash
set -euxo pipefail

BAKE_STATUS="OK"

report_done() {
  echo "BAKE_DONE:${BAKE_STATUS}" > /dev/ttyS0
}
trap report_done EXIT

export DEBIAN_FRONTEND=noninteractive

# ---- Install Docker CE ----
apt-get update -y
apt-get install -y ca-certificates curl gnupg lsb-release
install -m 0755 -d /etc/apt/keyrings
if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor --yes --batch --no-tty -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
fi
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
> /etc/apt/sources.list.d/docker.list
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

# ---- Install NVIDIA Container Toolkit ----
install -m 0755 -d /usr/share/keyrings

tmpkey=$(mktemp)
if ! curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey -o "$tmpkey"; then
  BAKE_STATUS="FAIL"
  exit 1
fi
gpg --dearmor --yes --batch --no-tty -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg "$tmpkey"
rm -f "$tmpkey"

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb #deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] #' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list

apt-get update -y
apt-get install -y nvidia-container-toolkit

nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

docker --version || true
nvidia-smi || true
"""

class PreVMImage:
    def __init__(
        self,
        project_id: str,
        zone: str,
        machine_type: str,
        gpu_type: str,
        gpu_count: int,
        boot_disk_gb: int,
        dlvm_family: str,
        bake_vm: str,
        custom_image_name: str,
        bake_timeout_sec: int,
        bake_poll_interval_sec: int
    ):
        self.project_id = project_id
        self.zone = f"{zone}-a"
        self.machine_type = machine_type
        self.gpu_type = gpu_type
        self.gpu_count = gpu_count
        self.boot_disk_gb = boot_disk_gb
        self.dlvm_family = dlvm_family
        self.bake_vm = bake_vm
        self.custom_image_name = custom_image_name
        self.bake_timeout_sec = bake_timeout_sec
        self.bake_poll_interval_sec = bake_poll_interval_sec

    @staticmethod
    def wait_for_extended_operation(op: ExtendedOperation, desc: str = ""):
        result = op.result(timeout=600)
        if op.error_code:
            raise RuntimeError(f"Error during {desc}: {op.error_message} (code {op.error_code})")
        if desc:
            logger.info(f"{desc} done.")
        return result

    def get_instance_serial_output(self, start: int | None = None) -> tuple[str, int | None]:
        inst_client = compute_v1.InstancesClient()
        req = GetSerialPortOutputInstanceRequest(
            project=self.project_id,
            zone=self.zone,
            instance=self.bake_vm,
            port=1,
        )
        if start is not None:
            req.start = start
        resp = inst_client.get_serial_port_output(request=req)
        contents = resp.contents or ""
        next_start = resp.next if getattr(resp, "next", None) is not None else None
        return contents, next_start

    def wait_for_bake_done(self):
        logger.info(f"Waiting for bake to finish on {self.bake_vm} …")
        deadline = time.time() + self.bake_timeout_sec
        start = None
        attempt = 0

        while time.time() < deadline:
            attempt += 1
            contents, next_start = self.get_instance_serial_output(start=start)

            # startup-script
            if "startup-script failed" in contents:
                raise RuntimeError("Startup script failed, see serial console for details")

            # BAKE_DONE:OK / BAKE_DONE:FAIL
            if "BAKE_DONE" in contents:
                if "BAKE_DONE:OK" in contents:
                    logger.info("Bake done (OK).")
                    return
                else:
                    raise RuntimeError("Bake script reported BAKE_DONE but with failure status")

            if next_start is not None:
                start = next_start

            logger.info(f"  try {attempt} … not done yet")
            time.sleep(self.bake_poll_interval_sec)

        raise RuntimeError("Bake did not finish in expected time")

    def delete_instance_if_exists(self):
        instance_client = compute_v1.InstancesClient()
        try:
            instance_client.get(project=self.project_id, zone=self.zone, instance=self.bake_vm)
        except NotFound:
            return
        except Conflict:
            logger.info("Conflict when checking existing instance — will try to delete.")
        logger.info(f"Instance {self.bake_vm} already exists — deleting it first.")
        del_op = instance_client.delete(project=self.project_id, zone=self.zone, instance=self.bake_vm)
        self.wait_for_extended_operation(del_op, f"delete existing instance {self.bake_vm}")

    def create_bake_instance(self):
        instance_client = compute_v1.InstancesClient()

        inst = Instance()
        inst.name = self.bake_vm
        inst.machine_type = f"zones/{self.zone}/machineTypes/{self.machine_type}"

        # GPU
        ga = AcceleratorConfig()
        ga.accelerator_type = f"projects/{self.project_id}/zones/{self.zone}/acceleratorTypes/{self.gpu_type}"
        ga.accelerator_count = self.gpu_count
        inst.guest_accelerators = [ga]

        # dask
        boot_disk = AttachedDisk()
        boot_disk.boot = True
        boot_disk.auto_delete = True
        init_params = AttachedDiskInitializeParams()
        init_params.source_image = f"projects/deeplearning-platform-release/global/images/family/{self.dlvm_family}"
        init_params.disk_size_gb = self.boot_disk_gb
        boot_disk.initialize_params = init_params
        inst.disks = [boot_disk]

        # network
        nic = NetworkInterface()
        nic.network = f"projects/{self.project_id}/global/networks/default"
        access = AccessConfig()
        access.name = "External NAT"
        access.type_ = compute_v1.AccessConfig.Type.ONE_TO_ONE_NAT.name
        nic.access_configs = [access]
        inst.network_interfaces = [nic]

        # scheduling
        sched = Scheduling()
        sched.on_host_maintenance = Scheduling.OnHostMaintenance.TERMINATE.name
        inst.scheduling = sched

        # metadata
        meta = Metadata()
        meta.items = [
            Items(
                key="startup-script",
                value=STARTUP_SCRIPT
            )
        ]
        inst.metadata = meta

        logger.info("Creating bake VM …")
        op = instance_client.insert(project=self.project_id, zone=self.zone, instance_resource=inst)
        self.wait_for_extended_operation(op, f"create instance {self.bake_vm}")

    def stop_instance(self):
        instance_client = compute_v1.InstancesClient()
        logger.info(f"Stopping VM {self.bake_vm} …")
        op = instance_client.stop(project=self.project_id, zone=self.zone, instance=self.bake_vm)
        self.wait_for_extended_operation(op, f"stop {self.bake_vm}")

    def create_image_from_instance_disk(self):
        image_client = compute_v1.ImagesClient()
        logger.info("Creating custom image …")

        img = Image()
        img.name = self.custom_image_name
        img.family = "dlvm-docker-gpu"
        img.source_disk = f"projects/{self.project_id}/zones/{self.zone}/disks/{self.bake_vm}"
        img.labels = {
            "base": "dlvm",
            "with": "docker-nvidia-toolkit",
        }

        op = image_client.insert(project=self.project_id, image_resource=img)
        self.wait_for_extended_operation(op, f"create image {self.custom_image_name}")

    def delete_instance(self):
        instance_client = compute_v1.InstancesClient()
        logger.info(f"Deleting bake VM {self.bake_vm} …")
        op = instance_client.delete(project=self.project_id, zone=self.zone, instance=self.bake_vm)
        self.wait_for_extended_operation(op, f"delete instance {self.bake_vm}")

    def create_vm_image(self):
        # 1. Cleaning up old instances
        self.delete_instance_if_exists()

        # 2. Create a new bake instance
        self.create_bake_instance()

        # 3. Wait for the startup-script to complete and write BAKE_DONE
        self.wait_for_bake_done()

        # 4. shutdown
        self.stop_instance()

        # 5. create vm image
        self.create_image_from_instance_disk()

        # 6. Delete temporary instance
        self.delete_instance()
        logger.info(
            f"✅ VM image created：{self.custom_image_name},(family: dlvm-docker-gpu, project: {self.project_id})"
        )

    def image_exists(self) -> bool:
        client = compute_v1.ImagesClient()
        try:
            client.get(project=self.project_id, image=self.custom_image_name)
            logger.info(f"VM image found: {self.custom_image_name}")
            return True
        except exceptions.NotFound:
            logger.info(f"No VM image found: {self.custom_image_name}")
            return False
        except Exception as e:
            logger.error(f"Error checking image: {e}")
            return False



if __name__ == "__main__":
    vm_image = PreVMImage(
        project_id = "ssc-ape-staging",
        zone= "asia-northeast1-a",
        machine_type="n1-standard-8",
        gpu_type="nvidia-tesla-t4",
        gpu_count=1,
        boot_disk_gb=100,
        dlvm_family="common-cu128-ubuntu-2204-nvidia-570",
        bake_vm="ml-dlvm",
        custom_image_name="test-ml-training-gpu-image",
        bake_timeout_sec=1200,
        bake_poll_interval_sec=20
    )

    image_exists = vm_image.image_exists()
    print(image_exists)
    if not image_exists:
        vm_image.create_vm_image()
