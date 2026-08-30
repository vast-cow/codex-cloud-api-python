from typing import Protocol
from ..models import Branch, Environment
class BranchProvider(Protocol):
    async def list_branches(self, environment: Environment) -> list[Branch]: ...
class StaticBranchProvider:
    def __init__(self, branches): self.branches = [Branch(x, source="static") if isinstance(x, str) else x for x in branches]
    async def list_branches(self, environment): return list(self.branches)
