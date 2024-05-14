# TODO: In the future, registering processing functions, compiling functions, and so on
import asyncio
from functools import wraps


def async_task(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        else:
            async def async_func():
                return func(*args, **kwargs)

            return await async_func()

    return wrapper


def async_nb_task(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        if asyncio.iscoroutinefunction(func):
            raise TypeError("@async_nb_task does not decorate asynchronous functions!")
        else:
            async_func = asyncio.to_thread(func, *args, **kwargs)
            return await async_func

    return wrapper
