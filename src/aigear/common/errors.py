import inspect
from typing import Any, Callable, Dict, Tuple


class CallException(Exception):
    """
    Base exception type for callables errors.
    """


class ParameterBindError(TypeError, CallException):
    """
    Raised when args and kwargs cannot be converted to parameters.
    """

    def __init__(self, msg: str):
        super().__init__(msg)

    @classmethod
    def from_bind_failure(
        cls, fn: Callable, exc: TypeError, call_args: Tuple[Any, ...], call_kwargs: Dict
    ):
        fn_signature = str(inspect.signature(fn)).strip("()")

        base = f"Error binding parameters for function '{fn.__name__}': {exc}"
        signature = f"Function '{fn.__name__}' has signature '{fn_signature}'"
        received = f"received args: {call_args} and kwargs: {list(call_kwargs.keys())}"
        msg = f"{base}.\n{signature} but {received}."
        return cls(msg)
