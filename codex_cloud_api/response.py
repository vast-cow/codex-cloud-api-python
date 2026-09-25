"""HTTP response abstraction."""
from dataclasses import dataclass
import json
from typing import Mapping, Any
from .exceptions import HTTPResponseError

@dataclass(frozen=True)
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes
    method: str
    url: str
    def text(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        return self.body.decode(encoding, errors)
    def json(self) -> Any:
        return json.loads(self.body)
    @property
    def ok(self) -> bool: return 200 <= self.status < 400
    def raise_for_status(self) -> None:
        if self.status >= 400: raise HTTPResponseError(self)
