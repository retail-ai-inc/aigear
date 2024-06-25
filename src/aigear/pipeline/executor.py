from __future__ import annotations
import ast
from concurrent.futures import (
    ThreadPoolExecutor,
    ProcessPoolExecutor,
    Future,
)
from typing import (
    Optional,
    Literal,
    Dict,
)
from ..common import logger
from ..common.callable import (
    parse_function,
    topological_sort,
    WrappedTask,
)


class TaskRunner:
    def __init__(
        self,
        max_workers: Optional[int] = None,
        executor: Optional[Literal["ThreadPool", "ProcessPool"]] = "ThreadPool"
    ):
        super().__init__()
        self._executor: Optional[ThreadPoolExecutor, ProcessPoolExecutor] = None
        self._max_workers = max_workers
        self.executor = executor
        self._namespace = {}
        self._nodes = []
        self._features: Dict[str, Future] = {}
        self._task_sorted = []
        self._tasks = {}

    def run_in_executor(self, fn: callable, pipeline_name: str = ""):
        self._tasks, dependencies = parse_function(fn)
        self._task_sorted = topological_sort(self._tasks, dependencies)
        logger.info(f"Task order: {[self._tasks.get(task_key).task_name for task_key in self._task_sorted]}")

        for task_key in self._task_sorted:
            task = self._tasks[task_key]
            self._run_code(task)
            self._run_function(task)

        return self._output()

    def _output(self):
        # Thread/process submission for assignment statements
        output_keys = []
        for task in self._tasks.values():
            key = task.output_key
            if isinstance(key, str):
                output_keys.append(task.output_key)
            elif isinstance(key, list):
                output_keys.extend(task.output_key)

        # Filter processing result in namespace
        outputs = {}
        for key in output_keys:
            output = self._namespace.get(key)
            if output is None:
                self._save_feature_result_to_namespace("", key)
                output = self._namespace.get(key)
            outputs[key] = output
        return outputs

    def _run_code(self, task: WrappedTask):
        if not task.is_feature:
            _ = self._get_args(task)
            _ = self._get_keywords(task)
            self._nodes.append(task.task)
        else:
            exec(compile(ast.Module(body=self._nodes, type_ignores=[]), filename="<ast>", mode="exec"), self._namespace)
            self._nodes.clear()

    def _run_function(self, task: WrappedTask):
        if task.is_feature:
            args = self._get_args(task)
            keywords = self._get_keywords(task)
            future = self._executor.submit(task.task, *args, **keywords)
            self._output_feature(task, future)

    def _output_feature(self, task: WrappedTask, future):
        if isinstance(task.output_key, str):
            self._features[task.output_key] = future
        else:
            self._features.update({k: [future, task.output_key] for k in task.output_key})

    def _get_keywords(self, task: WrappedTask):
        keywords_dict = {}
        for k, v in task.keywords.items():
            arg = self._namespace.get(v)
            if arg is None:
                self._save_feature_result_to_namespace(task.task_name, v)
                arg = self._namespace.get(v)
                keywords_dict[k] = arg
            else:
                keywords_dict[k] = arg
        return keywords_dict

    def _get_args(self, task: WrappedTask):
        args_list = []
        for k in task.args:
            arg = self._namespace.get(k)
            if arg is None:
                self._save_feature_result_to_namespace(task.task_name, k)
                arg = self._namespace.get(k)
                args_list.append(arg)
            else:
                args_list.append(arg)
        return args_list

    def _save_feature_result_to_namespace(self, task_name_current: str, k: str):
        feature = self._features.get(k)
        if feature is None:
            task_name_dependent, tasks_sorted = self._get_task_name(k)
            raise ValueError(
                f"Dependency error: {tasks_sorted}, `{task_name_current}` should be after `{task_name_dependent}`")

        if isinstance(feature, list):
            feature, output_keys = feature
            results = feature.result()
            self._namespace.update({k: v for k, v in zip(output_keys, results)})
        else:
            # TODO: Can't pickle <function> for ProcessPool.
            self._namespace[k] = feature.result()

    def _get_task_name(self, arg_key: str):
        tasks = [self._tasks[task_key] for task_key in self._task_sorted]
        task_name = [task.task_name for task in tasks if arg_key in task.output_key][0]
        tasks_sorted = [task.task_name for task in tasks]
        return task_name, tasks_sorted

    def submit(self, func: callable, *args, **kwargs):
        return self._executor.submit(func, *args, **kwargs)

    def __enter__(self):
        if self.executor == "ProcessPool":
            self._executor = ProcessPoolExecutor(
                max_workers=self._max_workers
            )
        else:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers
            )
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._executor is not None:
            self._executor.shutdown()
            self._executor = None
