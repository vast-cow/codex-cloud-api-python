"""Credential models and secure JSON persistence."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .exceptions import AuthenticationError

DEFAULT_CREDENTIAL_HOME = Path.home() / ".codex-cloud-api"


class CredentialStore(Protocol):
    description: str
    def load(self) -> dict[str, Any] | None: ...
    def save(self, value: dict[str, Any]) -> None: ...


class JsonCredentialStore:
    """Atomic, owner-only (on POSIX) JSON credential storage."""
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.description = str(self.path)

    def load(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError) as exc:
            raise AuthenticationError(f"could not read credentials from {self.path}: {exc}") from exc
        if not isinstance(value, dict):
            raise AuthenticationError(f"credentials in {self.path} must be a JSON object")
        return value

    def save(self, value: dict[str, Any]) -> None:
        existed = self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "posix" and not existed:
            os.chmod(self.path.parent, 0o700)
        fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        temporary = Path(name)
        try:
            if os.name == "posix": os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            if os.name == "posix": os.chmod(self.path, 0o600)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


@dataclass
class Credentials:
    access_token: str
    refresh_token: str | None
    account_id: str | None
    document: dict[str, Any]
    store: CredentialStore


def credentials_from_document(document: dict[str, Any], store: CredentialStore) -> Credentials:
    from .auth import account_id_from_id_token
    tokens = document.get("tokens")
    if not isinstance(tokens, dict):
        raise AuthenticationError(f"no ChatGPT OAuth tokens found in {store.description}")
    access = tokens.get("access_token")
    if not isinstance(access, str) or not access:
        raise AuthenticationError(f"access_token is missing in {store.description}")
    refresh = tokens.get("refresh_token")
    account = tokens.get("account_id") or account_id_from_id_token(tokens.get("id_token"))
    return Credentials(access, refresh if isinstance(refresh, str) and refresh else None,
                       account if isinstance(account, str) and account else None, document, store)


def load_credentials(credential_file: str | Path | None = None) -> Credentials | None:
    """Load credentials, returning ``None`` when the file does not exist."""
    path = credential_file or os.environ.get("CODEX_CLOUD_API_CREDENTIALS") or DEFAULT_CREDENTIAL_HOME / "credentials.json"
    store = JsonCredentialStore(path)
    document = store.load()
    return credentials_from_document(document, store) if document is not None else None

# Historical name, kept as a harmless convenience.
AuthJsonStore = JsonCredentialStore
