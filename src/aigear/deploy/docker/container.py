from .client import (
    docker,
    DockerException,
    silence_docker_warnings,
    APIError,
    NotFound,
)
from ...common import (
    logger,
)


class Container:
    def __init__(self):
        self.client = None
        self.container = None

    def run(self, image, command=None, stdout=True, stderr=False,
            remove=False, **kwargs):
        try:
            self.container = self.client.containers.run(
                image=image,
                command=command,
                stdout=stdout,
                stderr=stderr,
                remove=remove,
                **kwargs
            )
            print(f"Started Docker container {self.container.id}")
            return self.container
        except APIError as e:
            raise f"Failed to start Docker container: {str(e)}"

    def create(self, image, command=None, **kwargs):
        container_id = self.client.create(self, image, command=command, **kwargs)
        return container_id

    def stop(self):
        self.container.stop()

    def remove(self):
        self.container.remove()

    def logs(self):
        self.container.logs()

    def status(self):
        try:
            status = self.container.status()
            return status
        except APIError as e:
            print(f"Error: {e.explanation}")

    def wait(self):
        try:
            status = self.container.status()
            return status
        except APIError as e:
            print(f"Error: {e.explanation}")
        self.container.wait()

    def __enter__(self):
        try:
            with silence_docker_warnings():
                self.client = docker.DockerClient.from_env()
            return self
        except DockerException as exc:
            raise RuntimeError(
                "This error is often thrown because Docker is not running. Please ensure Docker is running."
            ) from exc

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            self.client.close()


def stream_logs(container):
    try:
        for log in container.logs(stream=True):
            yield log.decode('utf-8').strip()
    except APIError as e:
        yield f"Failed to stream logs from container: {e}"


def run_or_restart_container(container_name, image_id, flow_path, fn_name, volumes, ports, hostname, **kwargs):
    with Container() as container_instance:
        try:
            container = container_instance.client.containers.get(container_name)
            if container.status == "running":
                logger.info(f"Container '{container_name}' is already running")
            else:
                logger.info(f"Restarting container '{container_name}'")
                container.start()
        except NotFound:
            logger.info(f"Starting new container '{container_name}' from image '{image_id}'")
            container = container_instance.run(
                image=image_id,
                name=container_name,
                detach=True,
                command=f"run-workflow --script_path {flow_path} --function_name {fn_name}",
                volumes=volumes,
                ports=ports,
                hostname=hostname,
                **kwargs
            )
        logger.info("Streaming logs from container:")
        for line in stream_logs(container):
            logger.info(line)
