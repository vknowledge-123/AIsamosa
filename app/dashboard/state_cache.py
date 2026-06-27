from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class StateCache(Generic[T]):
    revision: int = -1
    value: T | None = None

    def __post_init__(self) -> None:
        self._lock = Lock()

    def get(self, revision: int) -> T | None:
        with self._lock:
            if self.revision == revision:
                return self.value
            return None

    def set(self, revision: int, value: T) -> T:
        with self._lock:
            self.revision = revision
            self.value = value
            return value

    def clear(self) -> None:
        with self._lock:
            self.revision = -1
            self.value = None

