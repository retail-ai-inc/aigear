# TODO: In the future, registering processing functions, compiling functions, and so on
from functools import wraps
import time
from ..common import logger, state, StateType


def task(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            start_time = time.time()
            result = func(*args, **kwargs)
            end_time = time.time()
            run_time = str(round(end_time - start_time, 3))
            state.set_state(func, StateType.COMPLETED, run_time)

            logger.info(f"Function '{func.__name__}' finished running")
            return result
        except Exception as e:
            logger.error(f"Function '{func.__name__}' failed running: {e}")
            state.set_state(func, StateType.FAILED)

    return wrapper
