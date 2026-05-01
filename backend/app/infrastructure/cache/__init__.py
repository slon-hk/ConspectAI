"""Cache infrastructure interfaces and implementations."""

from .base import CacheClient
from .null_cache import NullCache

__all__ = ["CacheClient", "NullCache"]
