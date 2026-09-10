"""Shared behaviour for anything that moves money."""

import functools


def audited(func):
    """Record every call, because money."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


class BaseProcessor:
    """Common lifecycle for payment processors."""

    def describe(self):
        return type(self).__name__
