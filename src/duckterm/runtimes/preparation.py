"""Optional native preparation inside the existing supervised terminal."""

from collections.abc import Callable
from typing import Protocol


class LaunchPreparation(Protocol):
    argv: list[str]
    env: dict[str, str]

    async def ready(self, pid: int, owns_process: Callable[[], bool]) -> None:
        """Verify the child and finish native preparation before admitting input."""
        ...

    async def close(self) -> None:
        """Close owned observation resources; never stop an unrelated process."""
        ...
