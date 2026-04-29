"""Database infrastructure primitives.

Legacy modules should import through ``db.py`` until their SQL is moved into
repositories. New code can depend on these primitives directly.
"""

from .pool import Database, database
from .transaction import TransactionManager

__all__ = ["Database", "TransactionManager", "database"]

