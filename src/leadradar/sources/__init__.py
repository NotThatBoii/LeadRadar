from collections.abc import Iterator
from typing import Protocol

from leadradar.models import RawBusiness


class Source(Protocol):
    name: str

    def collect(self, limit: int) -> Iterator[RawBusiness]: ...
