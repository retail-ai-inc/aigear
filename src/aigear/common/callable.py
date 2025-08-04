from __future__ import annotations
import ast
import inspect
from typing import Any, Callable, Dict, Tuple
from collections import defaultdict, deque
from .errors import ParameterBindError

SEPARATOR = "*"


def get_call_parameters(
    fn: Callable,
    call_args: Tuple[Any, ...],
    call_kwargs: Dict[str, Any],
) -> tuple:
    """
    Bind a call to a function to get parameter/value mapping. Default values on the
    signature will be included if not overridden.

    Raises a ParameterBindError if the arguments/kwargs are not valid for the function
    """
    try:
        bound_signature = inspect.signature(fn).bind(*call_args, **call_kwargs)
    except TypeError as exc:
        raise ParameterBindError.from_bind_failure(fn, exc, call_args, call_kwargs)

    return bound_signature.args, bound_signature.kwargs


class TaskVisitor(ast.NodeVisitor):
    def __init__(self, tasks=None, dependencies=None):
        if dependencies is None:
            dependencies = []
        if tasks is None:
            tasks = {}
        self._args_keys = {}
        self.tasks = tasks
        self.dependencies = dependencies

    def visit_Assign(self, node):
        args = []
        keywords = {}
        var_name, var_args, output_key = self._get_output_keys(node)
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            task_name = node.value.func.id
            if task_name in __builtins__:
                self._args_keys.update(var_args)
                self.tasks[var_name] = WrappedTask(
                    task_name=var_name,
                    task=node,
                    output_key=output_key,
                )
            else:
                args, keywords = self._get_args_keywords(node)
                self._args_keys.update(var_args)
                self.tasks[var_name] = WrappedTask(
                    task_name=task_name,
                    args=args,
                    keywords=keywords,
                    output_key=output_key,
                )
        elif isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
            args, keywords = self._get_args_keywords(node)
            self._args_keys.update(var_args)
            self.tasks[var_name] = WrappedTask(
                task_name=var_name,
                task=node,
                args=args,
                keywords=keywords,
                output_key=output_key,
            )
        else:
            self._args_keys.update(var_args)
            self.tasks[var_name] = WrappedTask(
                task_name=var_name,
                task=node,
                args=args,
                keywords=keywords,
                output_key=output_key,
            )
        self._get_dependencies(args, keywords, var_name)
        self.generic_visit(node)

    def visit_Expr(self, node):
        args = []
        keywords = {}
        var_name = ""
        # Handle expressions like function calls
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            var_name = node.value.func.id
            args, keywords = self._get_args_keywords(node)
            if var_name == "print":
                var_name = f"{var_name}-{len(self.tasks) + 1}"
            self.tasks[var_name] = WrappedTask(
                task_name=var_name,
                task=node,
                args=args,
                keywords=keywords,
            )
        elif isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute):
            var_name = f"{node.value.func.attr}-{len(self.tasks) + 1}"
            args, keywords = self._get_args_keywords(node)
            self.tasks[var_name] = WrappedTask(
                task_name=var_name,
                task=node,
                args=args,
                keywords=keywords,
            )

        self._get_dependencies(args, keywords, var_name)
        self.generic_visit(node)

    @staticmethod
    def _get_output_keys(node):
        target = node.targets[0]
        var_args = {}
        var_name = None
        out_keys = None
        illegal_keys = False
        if isinstance(target, ast.Name):
            var_name = out_keys = target.id
            illegal_keys = SEPARATOR not in var_name
        elif isinstance(target, ast.Tuple):
            out_keys = [sub_target.id for sub_target in target.dims if isinstance(sub_target, ast.Name)]
            var_name = f'{SEPARATOR}'.join(out_keys)
            var_args = {sub_target.id: var_name for sub_target in target.dims if isinstance(sub_target, ast.Name)}
            illegal_keys = all(True if SEPARATOR not in key else False for key in out_keys)

        if not illegal_keys:
            raise ValueError(f"Keywords cannot use {SEPARATOR}.")
        return var_name, var_args, out_keys

    def _get_dependencies(self, args: list, keywords: dict, var_name: str):
        args_list = [arg for arg in args if isinstance(arg, str)]
        for v in keywords.values():
            args_list.append(v)

        args_list = [self._args_keys.get(arg) if self._args_keys.get(arg) else arg for arg in args_list]
        for arg in set(args_list):
            self.dependencies.append((arg, var_name))

    @staticmethod
    def _get_arg_value(node):
        if isinstance(node, ast.Name):
            return node.id
        return node

    def _get_args_keywords(self, node):
        args = []
        keywords = {}
        if node.value.args:
            args = [self._get_arg_value(arg) for arg in node.value.args]
        if node.value.keywords:
            keywords = {keyword.arg: self._get_arg_value(keyword.value) for keyword in node.value.keywords}
        return args, keywords


def parse_function(func):
    func_globals = func.__globals__
    source = inspect.getsource(func)
    tree = ast.parse(source, )
    tasks = {}
    dependencies = []
    task_funcs = {}

    TaskVisitor(tasks, dependencies).visit(tree)
    for var, task in tasks.items():
        func_id = func_globals.get(task.task_name)
        if func_id is None:
            task.is_feature = False
        else:
            task.task = func_id
            task.is_feature = True
        task_funcs[var] = task
    return task_funcs, dependencies


def topological_sort(tasks, dependencies):
    graph = defaultdict(list)
    degrees = {task: 0 for task in tasks}

    for pre, post in dependencies:
        graph[pre].append(post)
        degrees[post] += 1

    queue = deque([task for task in tasks if degrees[task] == 0])
    order = []

    while queue:
        current = queue.popleft()
        order.append(current)
        for neighbor in graph[current]:
            degrees[neighbor] -= 1
            if degrees[neighbor] == 0:
                queue.append(neighbor)

    if len(order) == len(tasks):
        return order
    else:
        raise ValueError(
            "There exists a cycle in the dependencies, Assignment variables and function names cannot be duplicated.")


class WrappedTask:
    def __init__(self, task_name=None, task=None, args=None, keywords=None, output_key=None, is_feature=False):
        if output_key is None:
            output_key = []
        if keywords is None:
            keywords = {}
        if args is None:
            args = []
        self.task_name = task_name
        self.task = task
        self.args = args
        self.keywords = keywords
        self.output_key = output_key
        self.is_feature = is_feature

    def __repr__(self):
        return f"Task(task_name={self.task_name}, " \
               f"task={self.task}, args={self.args}, " \
               f"keywords={self.keywords}, output_key={self.output_key}, is_feature={self.is_feature})"
