from .task import Task
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any, Dict, Iterable, Optional, Set




from aigear.pipeline.types import RelyParams
import concurrent.futures


def step_executor(step, all_output_results):
    func = step.func
    params = step.params
    output = step.output_key

    all_params = []
    for parm in params:
        if isinstance(parm, RelyParams):
            rely_params = _expand_rely_params(parm, all_output_results)
            all_params.extend(rely_params)
        else:
            all_params.append(parm)
    # Run and save output results
    outputs = func(*all_params)

    if output.keywords:
        # Store parameters according to keywords
        results = {key: output for key, output in zip(output.keywords, outputs)}
    else:
        # When the output is a value, wrap it into a tuple
        results = outputs
    return results


def _expand_rely_params(params, all_output_results):
    rely_params = all_output_results.get(params.rely_output_key)
    if params.keywords:
        expanded_params = [rely_params[key] for key in params.keywords]
    elif isinstance(rely_params, tuple):
        expanded_params = rely_params
    else:
        expanded_params = [rely_params]
    return expanded_params


def thread_executor(steps, all_output_results):
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = [executor.submit(step_executor, step, all_output_results) for step in steps]
        results_dict = {step.output_key.key: future.result() for future, step in
                        zip(concurrent.futures.as_completed(futures), steps)}
    return results_dict


def process_executor(steps, all_output_results):
    with concurrent.futures.ProcessPoolExecutor() as executor:
        futures = [executor.submit(step_executor, step, all_output_results) for step in steps]
        results_dict = {step.output_key.key: future.result() for future, step in
                        zip(concurrent.futures.as_completed(futures), steps)}
    return results_dict


class ThreadPoolTaskRunner:
    def __init__(self):
        super().__init__()
        self._executor = None

    def duplicate(self) -> "ThreadPoolTaskRunner":
        return type(self)()

    def submit(
        self,
        task: "Task",
        parameters: Dict[str, Any],
        wait_for=None,
        dependencies=None,
    ):
        """
        Submit a task to thread pool.

        Args:
            task: The task to submit.
            parameters: The parameters to use when running the task.
            wait_for: A list of futures that the task depends on.

        Returns:
            A future object that can be used to wait for the task to complete and
            retrieve the result.
        """
        if not self._started or self._executor is None:
            raise RuntimeError("Task runner is not started")

        from prefect.context import FlowRunContext
        from prefect.task_engine import run_task_async, run_task_sync

        task_run_id = uuid.uuid4()
        context = copy_context()

        flow_run_ctx = FlowRunContext.get()
        if flow_run_ctx:
            get_run_logger(flow_run_ctx).info(
                f"Submitting task {task.name} to thread pool executor..."
            )
        else:
            self.logger.info(f"Submitting task {task.name} to thread pool executor...")

        future = self._executor.submit(
            context.run,
            run_task_sync,
            task=task,
            task_run_id=task_run_id,
            parameters=parameters,
            wait_for=wait_for,
            return_type="state",
            dependencies=dependencies,
        )
        prefect_future = PrefectConcurrentFuture(
            task_run_id=task_run_id, wrapped_future=future
        )
        return prefect_future

    def __enter__(self):
        super().__enter__()
        self._executor = ThreadPoolExecutor()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._executor is not None:
            self._executor.shutdown()
            self._executor = None
        super().__exit__(exc_type, exc_value, traceback)

