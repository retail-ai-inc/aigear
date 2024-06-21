import inspect
import time
import traceback
from typing import (
    Optional,
    Literal,
    Any,
)
from ..common import (
    logger,
    state,
)
from ..common.callable import get_call_parameters
from ..common.hashing import file_hash, stable_hash
from .executor import TaskRunner
from ..deploy.docker.builder import ImageBuilder
from ..deploy.docker.container import run_or_restart_container
from ..deploy.docker.utilities import flow_path_in_workdir


class WorkFlow:
    def __init__(
        self,
        fn: callable = None,
        name: str = None,
        description: str = None,
        tags: set[str] = None,
        version: str = None,
    ):
        self.state = None
        if not callable(fn):
            raise TypeError("'fn' must be callable")

        if name is not None and not isinstance(name, str):
            raise TypeError("Expected string for workflow parameter 'name'.")

        self.fn = fn
        self.name = name or fn.__name__.replace("_", "-")
        self.description = description or inspect.getdoc(fn)
        self.tags = tags
        self._flow_file = inspect.getsourcefile(self.fn)
        if not version:
            try:
                version = file_hash(self._flow_file)
            except (FileNotFoundError, TypeError, OSError):
                version = stable_hash(self.name)
        self.version = version

    def deploy(self, hostname=None, ports=None, volumes=None, skip_build_image=False, **kwargs):
        image_builder = ImageBuilder()
        if skip_build_image:
            image_id = image_builder.get_image_id(tag=self.name)
        else:
            image_id = image_builder.build(tag=self.name)
        flow_path = flow_path_in_workdir(self._flow_file)
        run_or_restart_container(
            self.name, image_id,
            flow_path,
            self.fn.__name__,
            volumes,
            ports,
            hostname,
            **kwargs
        )

    def run_in_executor(
        self,
        max_workers: Optional[int] = None,
        executor: Optional[Literal["ThreadPool", "ProcessPool"]] = "ThreadPool",
    ):
        outputs = None
        try:
            start_time = time.time()

            logger.info("Pipeline run start.")
            with TaskRunner(max_workers, executor) as runner:
                outputs = runner.run_in_executor(self.fn, self.name)
            logger.info("Pipeline run completed.")

            end_time = time.time()
            run_time = str(round(end_time - start_time, 3))
            logger.info(f"The total running time of the pipeline: {run_time}s")
        except Exception as e:
            logger.error(f"Pipeline '{self.name}' failed running: \n{traceback.format_exc()}")
        finally:
            self.state = state
            logger.info('\n' + state.state)
        return outputs

    def __call__(
        self,
        *args: tuple[Any, ...],
        **kwargs: dict[str, Any],
    ):
        result = None
        try:
            start_time = time.time()

            logger.info("Pipeline run start.")
            args, kwargs = get_call_parameters(self.fn, args, kwargs)
            result = self.fn(*args, **kwargs)
            logger.info("Pipeline run completed.")

            end_time = time.time()
            run_time = str(round(end_time - start_time, 3))
            logger.info(f"The total running time of the pipeline: {run_time}s")
        except Exception as e:
            logger.error(f"Pipeline '{self.name}' failed running: \n{traceback.format_exc()}")
        finally:
            self.state = state
            logger.info('\n' + state.state)
        return result


def workflow(
    __fn: callable = None,
    *,
    name: str = None,
    description: str = None,
    tags: set[str] = None,
    version: str = None,
):
    """
    Decorator to designate a function as a workflow.

    Args:
        __fn: Function that require decoration
        name: An optional name for the workflow; if not provided, the name will be inferred
            from the given function.
        description: An optional string description for the workflow.
        tags: An optional set of tags to be associated with runs of this workflow.
        version: An optional string specifying the version of this workflow definition

    Returns:
        A callable `workflow` object which, when called, will submit the workflow for execution.

    Examples:
        Define a simple workflow

        >>> from aigear.pipeline import task, workflow
        >>> from sklearn.datasets import load_iris
        >>> @task
        >>> def load_data():
        >>>     iris = load_iris()
        >>>     return iris

        >>> @workflow
        >>> def my_pipeline():
        >>>     iris = load_iris()

        >>> if __name__ == "__main__":
        >>>    # my_pipeline()  # or the following command
        >>>    my_pipeline.run_in_executor()
    """

    def decorator(fn):
        return WorkFlow(
            fn=fn,
            name=name,
            description=description,
            tags=tags,
            version=version,
        )

    if __fn is None:
        return decorator
    else:
        return decorator(__fn)
