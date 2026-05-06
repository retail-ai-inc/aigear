import time

from google.api_core import exceptions
from google.api_core.exceptions import Conflict, NotFound
from google.api_core.extended_operation import ExtendedOperation
from google.cloud import compute_v1
from google.cloud.compute_v1.types import (
    AcceleratorConfig,
    AccessConfig,
    AttachedDisk,
    AttachedDiskInitializeParams,
    GetSerialPortOutputInstanceRequest,
    Image,
    Instance,
    Items,
    Metadata,
    NetworkInterface,
    Scheduling,
)

from aigear.common.logger import Logging

logger = Logging(log_name=__name__).console_logging()

# ─── Startup Scripts ──────────────────────────────────────────────────────────

# Shared snippet: install gcloud CLI + kubectl (used by both CPU and GPU images)
_INSTALL_GCLOUD_KUBECTL = r"""
# ---- Install gcloud CLI ----
apt-get install -y apt-transport-https gnupg
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
  | gpg --dearmor --yes --batch --no-tty -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] \
https://packages.cloud.google.com/apt cloud-sdk main" \
  > /etc/apt/sources.list.d/google-cloud-sdk.list
apt-get update -y
apt-get install -y google-cloud-cli google-cloud-cli-gke-gcloud-auth-plugin

# ---- Install kubectl ----
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.32/deb/Release.key \
  | gpg --dearmor --yes --batch --no-tty -o /usr/share/keyrings/kubernetes-apt-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/kubernetes-apt-keyring.gpg] \
https://pkgs.k8s.io/core:/stable:/v1.32/deb/ /" \
  > /etc/apt/sources.list.d/kubernetes.list
apt-get update -y
apt-get install -y kubectl

gcloud --version || true
kubectl version --client || true
"""

# CPU image: Docker CE + gcloud CLI + kubectl (no GPU dependencies)
STARTUP_SCRIPT_CPU = r"""#!/usr/bin/env bash
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
""" + _INSTALL_GCLOUD_KUBECTL + r"""
docker --version || true
"""

# GPU image: Docker CE + NVIDIA Container Toolkit + gcloud CLI + kubectl
STARTUP_SCRIPT_GPU = r"""#!/usr/bin/env bash
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
gpg --dearmor --yes --batch --no-tty \
  -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg "$tmpkey"
rm -f "$tmpkey"

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb #deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] #' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update -y
apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker
""" + _INSTALL_GCLOUD_KUBECTL + r"""
docker --version || true
nvidia-smi || true
"""


# ─── PreVMImage ───────────────────────────────────────────────────────────────

class PreVMImage:
    """
    Baking tool for GPU and CPU custom VM images.

    GPU image (default name: ml-training-gpu-image):
      Based on DLVM (deeplearning-platform-release) + NVIDIA Container Toolkit
      + gcloud CLI + kubectl

    CPU image (default name: ml-training-cpu-image):
      Based on Ubuntu 22.04 + Docker CE + gcloud CLI + kubectl
    """

    def __init__(
        self,
        project_id: str,
        zone: str,
        # GPU bake parameters
        gpu_machine_type: str        = "n1-standard-8",
        gpu_type: str                = "nvidia-tesla-t4",
        gpu_count: int               = 1,
        gpu_boot_disk_gb: int        = 100,
        dlvm_family: str             = "common-cu129-ubuntu-2204-nvidia-580",
        gpu_bake_vm: str             = "ml-dlvm-gpu",
        # CPU bake parameters
        cpu_machine_type: str        = "e2-standard-4",
        cpu_boot_disk_gb: int        = 50,
        cpu_base_image: str          = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts",
        cpu_bake_vm: str             = "ml-dlvm-cpu",
        # Image names (customisable; defaults match the Cloud Function CONFIG)
        gpu_image_name: str          = "ml-training-gpu-image",
        cpu_image_name: str          = "ml-training-cpu-image",
        # Shared parameters
        bake_timeout_sec: int        = 1200,
        bake_poll_interval_sec: int  = 20,
    ):
        if not project_id:
            raise ValueError("project_id must not be empty")
        self.project_id = project_id

        # Normalise zone: accept both 'asia-northeast1' and 'asia-northeast1-a'
        # Strip trailing '-X' suffix to derive the region, then build a/b/c fallbacks
        region = zone[:-2] if (len(zone) >= 2 and zone[-2] == "-") else zone
        self.fallback_zones = [f"{region}-a", f"{region}-b", f"{region}-c"]
        self.zone = self.fallback_zones[0]  # active zone; updated on successful fallback

        # GPU
        self.gpu_machine_type = gpu_machine_type
        self.gpu_type         = gpu_type
        self.gpu_count        = gpu_count
        self.gpu_boot_disk_gb = gpu_boot_disk_gb
        self.dlvm_family      = dlvm_family
        self.gpu_bake_vm      = gpu_bake_vm

        # CPU
        self.cpu_machine_type = cpu_machine_type
        self.cpu_boot_disk_gb = cpu_boot_disk_gb
        self.cpu_base_image   = cpu_base_image
        self.cpu_bake_vm      = cpu_bake_vm

        # Image names
        self.gpu_image_name = gpu_image_name
        self.cpu_image_name = cpu_image_name

        # Shared
        self.bake_timeout_sec       = bake_timeout_sec
        self.bake_poll_interval_sec = bake_poll_interval_sec

    # ── Internal helpers ─────────────────────────────────────────────────────

    _EXHAUSTED_MSGS = (
        "ZONE_RESOURCE_POOL_EXHAUSTED",
        "STOCKOUT",
        "stockout",
        "SERVICE UNAVAILABLE",
        "acceleratorTypes",
    )

    _SUBNET_NOT_READY_MSG = "is not ready"
    _SUBNET_NOT_READY_RETRIES = 5
    _SUBNET_NOT_READY_WAIT_SEC = 30

    @staticmethod
    def _is_subnet_not_ready(exc: Exception) -> bool:
        return "subnetworks" in str(exc) and "is not ready" in str(exc)

    @classmethod
    def _is_exhausted(cls, exc: Exception) -> bool:
        # Only treat 404 as a zone-skip if it concerns an accelerator resource;
        # all other 404s (wrong project, missing image, etc.) should propagate.
        from google.api_core.exceptions import NotFound
        if isinstance(exc, NotFound) and "acceleratorTypes" not in str(exc):
            return False
        msg = str(exc)
        return any(kw in msg for kw in cls._EXHAUSTED_MSGS)

    @staticmethod
    def _wait_op(op: ExtendedOperation, desc: str = "") -> object:
        result = op.result(timeout=600)
        if op.error_code:
            raise RuntimeError(
                f"Error during {desc}: {op.error_message} (code {op.error_code})"
            )
        if desc:
            logger.info(f"{desc} done.")
        return result

    def _get_serial_output(
        self, bake_vm: str, start: int | None = None, zone: str | None = None
    ) -> tuple[str, int | None]:
        client = compute_v1.InstancesClient()
        req = GetSerialPortOutputInstanceRequest(
            project=self.project_id,
            zone=zone or self.zone,
            instance=bake_vm,
            port=1,
        )
        if start is not None:
            req.start = start
        resp = client.get_serial_port_output(request=req)
        contents   = resp.contents or ""
        next_start = resp.next if getattr(resp, "next", None) is not None else None
        return contents, next_start

    def _wait_bake_done(self, bake_vm: str, zone: str | None = None):
        logger.info(f"Waiting for bake to finish on {bake_vm} ...")
        deadline = time.time() + self.bake_timeout_sec
        start    = None
        attempt  = 0

        while time.time() < deadline:
            attempt += 1
            contents, next_start = self._get_serial_output(bake_vm, start=start, zone=zone)

            if "startup-script failed" in contents:
                raise RuntimeError("Startup script failed, see serial console for details")
            if "BAKE_DONE" in contents:
                if "BAKE_DONE:OK" in contents:
                    logger.info("Bake done (OK).")
                    return
                raise RuntimeError("Bake script reported BAKE_DONE but with failure status")

            if next_start is not None:
                start = next_start

            logger.info(f"  attempt {attempt} ... not done yet")
            time.sleep(self.bake_poll_interval_sec)

        raise RuntimeError(f"Bake did not finish within {self.bake_timeout_sec}s")

    def _delete_instance_if_exists(self, bake_vm: str, zone: str | None = None):
        z = zone or self.zone
        client = compute_v1.InstancesClient()
        try:
            client.get(project=self.project_id, zone=z, instance=bake_vm)
        except NotFound:
            return
        except Conflict:
            logger.info("Conflict when checking existing instance - will try to delete.")
        logger.info(f"Instance {bake_vm} already exists - deleting it first.")
        op = client.delete(project=self.project_id, zone=z, instance=bake_vm)
        self._wait_op(op, f"delete existing instance {bake_vm}")

    def _stop_instance(self, bake_vm: str, zone: str | None = None):
        z = zone or self.zone
        client = compute_v1.InstancesClient()
        logger.info(f"Stopping VM {bake_vm} ...")
        op = client.stop(project=self.project_id, zone=z, instance=bake_vm)
        self._wait_op(op, f"stop {bake_vm}")

    def _create_image_from_disk(
        self, bake_vm: str, image_name: str, image_family: str, extra_labels: dict,
        zone: str | None = None
    ):
        z = zone or self.zone
        client = compute_v1.ImagesClient()
        logger.info(f"Creating image {image_name} from disk of {bake_vm} ...")

        img             = Image()
        img.name        = image_name
        img.family      = image_family
        img.source_disk = f"projects/{self.project_id}/zones/{z}/disks/{bake_vm}"
        img.labels      = extra_labels

        op = client.insert(project=self.project_id, image_resource=img)
        self._wait_op(op, f"create image {image_name}")

    def _delete_instance(self, bake_vm: str, zone: str | None = None):
        z = zone or self.zone
        client = compute_v1.InstancesClient()
        logger.info(f"Deleting bake VM {bake_vm} ...")
        op = client.delete(project=self.project_id, zone=z, instance=bake_vm)
        self._wait_op(op, f"delete instance {bake_vm}")

    def _image_exists(self, image_name: str) -> bool:
        client = compute_v1.ImagesClient()
        try:
            client.get(project=self.project_id, image=image_name)
            return True
        except exceptions.NotFound:
            return False
        except Exception as e:
            logger.error(f"Error checking image {image_name}: {e}")
            raise

    def _build_metadata(self, startup_script: str) -> Metadata:
        meta = Metadata()
        meta.items = [Items(key="startup-script", value=startup_script)]
        return meta

    def _create_instance_with_fallback(
        self,
        build_instance_fn,  # (zone: str) -> Instance
        bake_vm: str,
        label: str,
    ) -> str:
        """
        Try to insert a bake VM across fallback zones.
        Returns the zone where the VM was successfully created.
        """
        client   = compute_v1.InstancesClient()
        last_exc: Exception | None = None

        for z in self.fallback_zones:
            logger.info(f"Trying zone {z} for {label} ...")
            inst = build_instance_fn(z)
            for attempt in range(1, self._SUBNET_NOT_READY_RETRIES + 1):
                try:
                    op = client.insert(project=self.project_id, zone=z, instance_resource=inst)
                    self._wait_op(op, f"create {label} in {z}")
                    logger.info(f"Bake VM {bake_vm} created in zone {z}.")
                    return z
                except Exception as exc:
                    if self._is_subnet_not_ready(exc):
                        if attempt < self._SUBNET_NOT_READY_RETRIES:
                            logger.warning(
                                f"Default subnet not ready yet (attempt {attempt}/{self._SUBNET_NOT_READY_RETRIES}), "
                                f"retrying in {self._SUBNET_NOT_READY_WAIT_SEC}s ..."
                            )
                            time.sleep(self._SUBNET_NOT_READY_WAIT_SEC)
                            inst = build_instance_fn(z)
                            continue
                        last_exc = exc
                        break
                    if self._is_exhausted(exc):
                        logger.warning(f"Zone {z} exhausted, trying next ...")
                        last_exc = exc
                        break
                    raise

        raise RuntimeError(
            f"All fallback zones exhausted for {label}."
        ) from last_exc

    # ── GPU image ────────────────────────────────────────────────────────────

    def _create_gpu_bake_instance(self):
        def build(zone: str) -> Instance:
            inst              = Instance()
            inst.name         = self.gpu_bake_vm
            inst.machine_type = f"zones/{zone}/machineTypes/{self.gpu_machine_type}"

            ga                   = AcceleratorConfig()
            ga.accelerator_type  = (
                f"projects/{self.project_id}/zones/{zone}"
                f"/acceleratorTypes/{self.gpu_type}"
            )
            ga.accelerator_count    = self.gpu_count
            inst.guest_accelerators = [ga]

            boot_disk                   = AttachedDisk()
            boot_disk.boot              = True
            boot_disk.auto_delete       = True
            init_params                 = AttachedDiskInitializeParams()
            init_params.source_image    = (
                f"projects/deeplearning-platform-release/global/images/family/{self.dlvm_family}"
            )
            init_params.disk_size_gb    = self.gpu_boot_disk_gb
            boot_disk.initialize_params = init_params
            inst.disks                  = [boot_disk]

            nic                     = NetworkInterface()
            nic.network             = f"projects/{self.project_id}/global/networks/default"
            access                  = AccessConfig()
            access.name             = "External NAT"
            access.type_            = compute_v1.AccessConfig.Type.ONE_TO_ONE_NAT.name
            nic.access_configs      = [access]
            inst.network_interfaces = [nic]

            sched                     = Scheduling()
            sched.on_host_maintenance = Scheduling.OnHostMaintenance.TERMINATE.name
            inst.scheduling           = sched

            inst.metadata = self._build_metadata(STARTUP_SCRIPT_GPU)
            return inst

        return self._create_instance_with_fallback(build, self.gpu_bake_vm, "GPU bake VM")

    def create_gpu_image(self):
        """Bake the GPU custom VM image (name: self.gpu_image_name)."""
        self._delete_instance_if_exists(self.gpu_bake_vm)
        zone = self._create_gpu_bake_instance()
        self._wait_bake_done(self.gpu_bake_vm, zone=zone)
        self._stop_instance(self.gpu_bake_vm, zone=zone)
        self._create_image_from_disk(
            bake_vm      = self.gpu_bake_vm,
            image_name   = self.gpu_image_name,
            image_family = "ml-training-gpu",
            extra_labels = {"base": "dlvm", "with": "docker-nvidia-kubectl"},
            zone         = zone,
        )
        self._delete_instance(self.gpu_bake_vm, zone=zone)
        logger.info(
            f"GPU image created: {self.gpu_image_name} "
            f"(family: ml-training-gpu, project: {self.project_id})"
        )

    def gpu_image_exists(self) -> bool:
        return self._image_exists(self.gpu_image_name)

    # ── CPU image ────────────────────────────────────────────────────────────

    def _create_cpu_bake_instance(self):
        def build(zone: str) -> Instance:
            inst              = Instance()
            inst.name         = self.cpu_bake_vm
            inst.machine_type = f"zones/{zone}/machineTypes/{self.cpu_machine_type}"

            boot_disk                   = AttachedDisk()
            boot_disk.boot              = True
            boot_disk.auto_delete       = True
            init_params                 = AttachedDiskInitializeParams()
            init_params.source_image    = self.cpu_base_image
            init_params.disk_size_gb    = self.cpu_boot_disk_gb
            boot_disk.initialize_params = init_params
            inst.disks                  = [boot_disk]

            nic                     = NetworkInterface()
            nic.network             = f"projects/{self.project_id}/global/networks/default"
            access                  = AccessConfig()
            access.name             = "External NAT"
            access.type_            = compute_v1.AccessConfig.Type.ONE_TO_ONE_NAT.name
            nic.access_configs      = [access]
            inst.network_interfaces = [nic]

            # CPU instances support live migration; MIGRATE is fine
            sched                     = Scheduling()
            sched.on_host_maintenance = Scheduling.OnHostMaintenance.MIGRATE.name
            inst.scheduling           = sched

            inst.metadata = self._build_metadata(STARTUP_SCRIPT_CPU)
            return inst

        return self._create_instance_with_fallback(build, self.cpu_bake_vm, "CPU bake VM")

    def create_cpu_image(self):
        """Bake the CPU custom VM image (name: self.cpu_image_name)."""
        self._delete_instance_if_exists(self.cpu_bake_vm)
        zone = self._create_cpu_bake_instance()
        self._wait_bake_done(self.cpu_bake_vm, zone=zone)
        self._stop_instance(self.cpu_bake_vm, zone=zone)
        self._create_image_from_disk(
            bake_vm      = self.cpu_bake_vm,
            image_name   = self.cpu_image_name,
            image_family = "ml-training-cpu",
            extra_labels = {"base": "ubuntu-2204", "with": "docker-kubectl"},
            zone         = zone,
        )
        self._delete_instance(self.cpu_bake_vm, zone=zone)
        logger.info(
            f"CPU image created: {self.cpu_image_name} "
            f"(family: ml-training-cpu, project: {self.project_id})"
        )

    def cpu_image_exists(self) -> bool:
        return self._image_exists(self.cpu_image_name)

    # ── Build both images ────────────────────────────────────────────────────

    def create_all_images(self, skip_existing: bool = True):
        """
        Bake both GPU and CPU images.
        When skip_existing=True (default), already-existing images are skipped.
        """
        if skip_existing and self.gpu_image_exists():
            logger.info(f"GPU image already exists, skipping: {self.gpu_image_name}")
        else:
            self.create_gpu_image()

        if skip_existing and self.cpu_image_exists():
            logger.info(f"CPU image already exists, skipping: {self.cpu_image_name}")
        else:
            self.create_cpu_image()

    # ── Delete images ────────────────────────────────────────────────────────

    def _delete_image(self, image_name: str):
        client = compute_v1.ImagesClient()
        logger.info(f"Deleting image {image_name} ...")
        try:
            op = client.delete(project=self.project_id, image=image_name)
        except NotFound:
            logger.warning(f"Image {image_name} not found, skipping delete.")
            return
        self._wait_op(op, f"delete image {image_name}")

    def delete_gpu_image(self):
        if self._image_exists(self.gpu_image_name):
            self._delete_image(self.gpu_image_name)
            logger.info(f"GPU image ({self.gpu_image_name}) deleted successfully.")
        else:
            logger.info(f"GPU image ({self.gpu_image_name}) not found. Skipping.")

    def delete_cpu_image(self):
        if self._image_exists(self.cpu_image_name):
            self._delete_image(self.cpu_image_name)
            logger.info(f"CPU image ({self.cpu_image_name}) deleted successfully.")
        else:
            logger.info(f"CPU image ({self.cpu_image_name}) not found. Skipping.")

    def delete(self):
        self.delete_gpu_image()
        self.delete_cpu_image()


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Corresponds to config:
    # "pre_vm_image": {
    #     "on": false,
    #     "machine_type": "n1-standard-8",   <- gpu_machine_type
    #     "gpu_type": "nvidia-tesla-t4",
    #     "gpu_count": 1,
    #     "boot_disk_gb": 100,               <- gpu_boot_disk_gb
    #     "dlvm_family": "common-cu128-ubuntu-2204-nvidia-570",
    #     "bake_vm": "ml-dlvm",              <- gpu_bake_vm
    #     "bake_timeout_sec": 1200,
    #     "bake_poll_interval_sec": 20
    # }
    pre = PreVMImage(
        project_id             = "",
        zone                   = "asia-northeast1-a",
        # GPU
        gpu_machine_type       = "n1-standard-8",
        gpu_type               = "nvidia-tesla-t4",
        gpu_count              = 1,
        gpu_boot_disk_gb       = 100,
        dlvm_family            = "common-cu129-ubuntu-2204-nvidia-580",
        gpu_bake_vm            = "ml-dlvm",
        # CPU
        cpu_machine_type       = "e2-standard-4",
        cpu_boot_disk_gb       = 50,
        cpu_base_image         = "projects/ubuntu-os-cloud/global/images/family/ubuntu-2204-lts",
        cpu_bake_vm            = "ml-dlvm-cpu",
        # Image names (defaults are fine; override if needed)
        gpu_image_name         = "ml-training-gpu-image",
        cpu_image_name         = "ml-training-cpu-image",
        # Shared
        bake_timeout_sec       = 1200,
        bake_poll_interval_sec = 20,
    )

    # Create individually
    # pre.create_gpu_image()
    # pre.create_cpu_image()

    # Create both (skip if already exist)
    pre.create_all_images(skip_existing=True)
    