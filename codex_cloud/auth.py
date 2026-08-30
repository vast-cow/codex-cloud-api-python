"""Authentication providers. Tokens are deliberately never read from CLI files."""
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

class AuthProvider(Protocol):
    async def headers(self) -> Mapping[str, str]: ...

@dataclass(frozen=True)
class StaticBearerAuth:
    token: str
    account_id: str | None = None
    async def headers(self) -> Mapping[str, str]:
        result = {"Authorization": f"Bearer {self.token}"}
        if self.account_id:
            result["ChatGPT-Account-Id"] = self.account_id
        return result

class RefreshingAuthProvider:
    def __init__(self, callback: Callable[[], Awaitable[Mapping[str, str]]]):
        self._callback = callback
    async def headers(self) -> Mapping[str, str]:
        return await self._callback()
