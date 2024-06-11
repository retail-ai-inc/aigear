import ast
import inspect
from typing import Any, Callable, Dict, Tuple
from collections import defaultdict, deque
from .errors import ParameterBindError


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
        self.current_task = None
        self.args_keys = {}
        self.tasks = tasks
        self.dependencies = dependencies

    def visit_Assign(self, node):
        print(node.value)
        exc_lines = []
        if isinstance(node.value, ast.Constant):
            print(node.value)
            # global k
            # k = node.value.value
        if isinstance(node.value, ast.BinOp):
            module_ast = ast.Module(body=[ast.Expr(node.value)])
            exc_lines.append(module_ast)
            result = eval(compile(module_ast, filename='', mode='exec'))
            print(result)
            # result = eval(compile(ast.Expression(body=node.value), filename='', mode='eval'))
            # print(result)
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
            task_name = node.value.func.id
            args = [i.id for i in node.value.args]
            target = node.targets[0]
            if isinstance(target, ast.Tuple):
                out_args = [sub.id for sub in target.dims]
                var_name = '-'.join(out_args)
                self.args_keys.update({sub.id: var_name for sub in target.dims})
            else:
                var_name = out_args = target.id
            self.tasks[var_name] = [task_name, args, out_args]
            self.current_task = var_name
            self.visit(node.value)

    def visit_Call(self, node):
        if self.current_task and isinstance(node.func, ast.Name):
            for arg in node.args:
                if isinstance(arg, ast.Name):
                    arg_id = self.args_keys.get(arg.id)
                    if arg_id:
                        self.dependencies.append((arg_id, self.current_task))
                    else:
                        self.dependencies.append((arg.id, self.current_task))


def parse_function(func):
    func_globals = func.__globals__
    source = inspect.getsource(func)
    tree = ast.parse(source, )
    tasks = {}
    dependencies = []

    TaskVisitor(tasks, dependencies).visit(tree)
    task_funcs = {var: [func_globals[task[0]], task[1], task[2]] for var, task in tasks.items()}
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
        raise ValueError("There exists a cycle in the dependencies")


illegal_expr = [
    ast.BoolOp,
    ast.NamedExpr,
    ast.BinOp,
    ast.UnaryOp,
    ast.Lambda,
    ast.IfExp,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
    ast.Compare,
    ast.FormattedValue,
    ast.JoinedStr,
    ast.Attribute,
    ast.Subscript,
    ast.Name,
    ast.List,
    ast.Tuple,
    ast.Slice,
]

expr = [
    ast.BoolOp,
    ast.NamedExpr,
    ast.BinOp,
    ast.UnaryOp,
    ast.Lambda,
    ast.IfExp,
    ast.Dict,
    ast.Set,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
    ast.Compare,
    ast.Call,
    ast.FormattedValue,
    ast.JoinedStr,
    ast.Constant,
    ast.Attribute,
    ast.Subscript,
    ast.Name,
    ast.List,
    ast.Tuple,
    ast.Slice,
]
