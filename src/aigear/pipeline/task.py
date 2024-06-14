import inspect
import time
import __main__
import traceback
from typing import (
    Optional,
    Literal,
    Any,
)
from ..common import logger, state, StateType
from ..common.callable import get_call_parameters
from .executor import TaskRunner


class Task:
    def __init__(
        self,
        fn: callable = None,
        name: str = None,
        description: str = None,
        tags: set[str] = None,
        version: str = None,
    ):
        if not callable(fn):
            raise TypeError("'fn' must be callable")

        self.fn = fn
        if not name:
            if not hasattr(self.fn, "__name__"):
                self.name = type(self.fn).__name__
            else:
                self.name = self.fn.__name__
        else:
            self.name = name
        self.description = description or inspect.getdoc(fn)
        self.tags = tags
        self.version = version

        state.set_state(fn, StateType.PENDING)

    def run_in_executor(
        self,
        *args: tuple[Any, ...],
        max_workers: Optional[int] = 1,
        executor: Optional[Literal["ThreadPool", "ProcessPool"]] = "ThreadPool",
        **kwargs: dict[str, Any],
    ):
        outputs = None
        try:
            start_time = time.time()

            with TaskRunner(max_workers, executor) as runner:
                setattr(__main__, self.fn.__name__, self.fn)
                feature = runner.submit(self.fn, *args, **kwargs)
                outputs = feature.result()

            end_time = time.time()
            run_time = str(round(end_time - start_time, 3))
            logger.info(f"The total running time of the Task: {run_time}s")
        except Exception as e:
            logger.error(f"Task '{self.name}' failed running: \n{traceback.format_exc()}")
        return outputs

    def __call__(
        self,
        *args: tuple[Any, ...],
        **kwargs: dict[str, Any],
    ):
        try:
            start_time = time.time()
            args, kwargs = get_call_parameters(self.fn, args, kwargs)
            result = self.fn(*args, **kwargs)
            end_time = time.time()
            run_time = str(round(end_time - start_time, 3))
            state.set_state(self.fn, StateType.COMPLETED, run_time)

            logger.info(f"Function '{self.fn.__name__}' finished running")
            return result
        except Exception as e:
            logger.error(f"Function '{self.fn.__name__}' failed running: \n{traceback.format_exc()}")
            state.set_state(self.fn, StateType.FAILED)


def task(
    __fn: callable = None,
    *,
    name: str = None,
    description: str = None,
    tags: set[str] = None,
    version: str = None,
):
    """
    Decorator to designate a function as a task in a workflow.

    Args:
        __fn: Function that require decoration
        name: An optional name for the task; if not provided, the name will be inferred
            from the given function.
        description: An optional string description for the task.
        tags: An optional set of tags to be associated with runs of this task. These
            tags are combined with any tags defined by a `aigear.tags` context at
            task runtime.
        version: An optional string specifying the version of this task definition

    Returns:
        A callable `Task` object which, when called, will submit the task for execution.

    Examples:
        Define a simple task

        >>> @task
        >>> def my_task(x, y):
        >>>     return x + y

        Define a task with a custom name
        >>> @task(name="The Custom Task")
        >>> def my_task():
        >>>     pass
    """

    def decorator(fn):
        return Task(
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
