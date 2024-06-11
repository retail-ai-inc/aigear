from functools import update_wrapper
import inspect
import traceback
from ..common import (
    logger,
    state,
)
from ..common.callables import get_call_parameters
from ..common.hashing import file_hash, stable_hash


class WorkFlow:
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

        if name is not None and not isinstance(name, str):
            raise TypeError("Expected string for flow parameter 'name'.")

        update_wrapper(self, fn)
        self.fn = fn
        self.name = name or fn.__name__.replace("_", "-")
        self.description = description or inspect.getdoc(fn)
        self.tags = tags
        if not version:
            try:
                flow_file = inspect.getsourcefile(self.fn)
                version = file_hash(flow_file)
            except (FileNotFoundError, TypeError, OSError):
                version = stable_hash(self.name)
        self.version = version

    def deploy(self, platform=None):
        # service// train
        pass

    def __call__(self, *args, **kwargs):
        result = None
        try:
            logger.info("Pipeline run start.")
            args, kwargs = get_call_parameters(self.fn, args, kwargs)
            result = self.fn(*args, **kwargs)
            logger.info("Pipeline run completed.")
        except Exception as e:
            logger.error(f"Pipeline '{self.name}' failed running: \n{traceback.format_exc()}")
        finally:
            self.state = state
            logger.info('\n' + state.state)
        return result
