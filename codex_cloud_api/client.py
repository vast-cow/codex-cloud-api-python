"""High-level authenticated Codex Cloud HTTP client."""
from __future__ import annotations
import asyncio, json, logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlencode, urlsplit, urlunsplit
import aiohttp
from .auth import REFRESH_TOKEN_URL, DeviceCode, access_token_expiration, acquire_credentials, oauth_client_id
from .credentials import Credentials, load_credentials, credentials_from_document
from .exceptions import AuthenticationError, ConfigurationError, NetworkError, TokenRefreshError, UnsafeDestinationError
from .response import Response

DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"
TRUSTED_HOSTS = {"chatgpt.com", "chat.openai.com", "chatgpt-staging.com"}
RESERVED_HEADERS = {"authorization", "chatgpt-account-id", "host", "cookie"}
log = logging.getLogger(__name__)

def _base_url(value: str) -> str:
    p = urlsplit(value); host = (p.hostname or "").lower()
    if p.scheme != "https" or host not in TRUSTED_HOSTS or p.username or p.password or p.query or p.fragment or p.port not in (None,443):
        raise UnsafeDestinationError("refusing to send credentials to an untrusted URL")
    return urlunsplit(("https", p.netloc, p.path.rstrip("/") or "/backend-api", "", ""))

def _url(base_url: str, path: str, params: Mapping[str, Any] | None) -> str:
    p = urlsplit(path)
    if p.scheme or p.netloc: raise UnsafeDestinationError("request target must be a relative API path")
    if p.query or p.fragment: raise ConfigurationError("pass query parameters with params; fragments are unsupported")
    request_path = p.path if p.path.startswith("/") else "/" + p.path
    final = request_path if request_path == "/backend-api" or request_path.startswith("/backend-api/") or request_path == "/api/codex" or request_path.startswith("/api/codex/") else urlsplit(base_url).path.rstrip("/") + request_path
    return urlunsplit(("https", urlsplit(base_url).netloc, final, urlencode(params or {}, doseq=True), ""))

class CodexCloudClient:
    """Async client encapsulating credentials, refresh, retries, and safe URLs."""
    def __init__(self, *, credential_file: str | Path | None = None, base_url: str = DEFAULT_BASE_URL,
                 account_id: str | None = None, timeout: float = 60.0, no_refresh: bool = False,
                 on_user_code: Callable[[DeviceCode], Any] | None = None,
                 session: aiohttp.ClientSession | None = None):
        if timeout <= 0: raise ConfigurationError("timeout must be greater than zero")
        self.credential_file, self.base_url, self.account_id = credential_file, _base_url(base_url), account_id
        self.timeout, self.no_refresh, self.on_user_code = timeout, no_refresh, on_user_code
        self._session, self._owns_session, self._credentials = session, session is None, None
        self._refresh_lock = asyncio.Lock()

    async def __aenter__(self):
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout, connect=min(10,self.timeout)))
        await self.authenticate(); return self

    async def __aexit__(self, *args): await self.close()
    async def close(self):
        if self._owns_session and self._session is not None: await self._session.close()
        self._session = None

    async def authenticate(self) -> Credentials:
        """Load credentials or automatically initiate Device Code login."""
        if self._credentials is None:
            self._credentials = await acquire_credentials(self.credential_file, timeout=self.timeout, on_user_code=self.on_user_code)
        return self._credentials

    @property
    def credentials(self) -> Credentials:
        if self._credentials is None: raise AuthenticationError("client is not authenticated")
        return self._credentials

    def _reload(self) -> bool:
        latest = load_credentials(self.credential_file)
        if latest is None: return False
        old_account, new_account = self.account_id or self.credentials.account_id, self.account_id or latest.account_id
        if old_account and new_account and old_account != new_account:
            raise AuthenticationError("stored credentials changed to a different ChatGPT account/workspace")
        changed = latest.access_token != self.credentials.access_token
        if changed: self._credentials = latest
        return changed

    def _needs_refresh(self) -> bool:
        expiry = access_token_expiration(self.credentials.access_token)
        if expiry: return expiry <= datetime.now(timezone.utc) + timedelta(minutes=5)
        raw = self.credentials.document.get("last_refresh")
        try: last = datetime.fromisoformat(raw.replace("Z","+00:00")) if isinstance(raw,str) else None
        except ValueError: last = None
        return bool(last and last < datetime.now(timezone.utc) - timedelta(days=8))

    async def refresh(self) -> None:
        """Refresh OAuth credentials and persist refresh-token rotation."""
        async with self._refresh_lock:
            if self._reload(): return
            token = self.credentials.refresh_token
            if not token: raise TokenRefreshError("credentials contain no refresh token")
            assert self._session is not None
            try:
                async with self._session.post(REFRESH_TOKEN_URL, json={"client_id":oauth_client_id(),"grant_type":"refresh_token","refresh_token":token}, headers={"Accept":"application/json","Content-Type":"application/json"}, allow_redirects=False) as response:
                    text = await response.text()
                    if not 200 <= response.status < 300: raise TokenRefreshError(f"OAuth token refresh failed: HTTP {response.status}")
                    try: refreshed = json.loads(text)
                    except json.JSONDecodeError as exc: raise TokenRefreshError("OAuth refresh returned invalid JSON") from exc
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc: raise NetworkError("OAuth refresh network request failed") from exc
            if not isinstance(refreshed, dict): raise TokenRefreshError("OAuth refresh returned an invalid response")
            document = self.credentials.store.load() or self.credentials.document
            tokens = document.get("tokens")
            if not isinstance(tokens, dict): raise TokenRefreshError("credential file has an incompatible format")
            for key in ("id_token","access_token","refresh_token"):
                if isinstance(refreshed.get(key),str) and refreshed[key]: tokens[key] = refreshed[key]
            document["last_refresh"] = datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
            self.credentials.store.save(document); self._credentials = credentials_from_document(document,self.credentials.store)

    def _headers(self, supplied: Mapping[str,str] | None) -> dict[str,str]:
        supplied = supplied or {}
        for name in supplied:
            if name.lower() in RESERVED_HEADERS: raise ConfigurationError(f"header {name!r} is managed by the client")
        result = {"Authorization":f"Bearer {self.credentials.access_token}","Accept":"application/json","User-Agent":"codex-cloud-api/1"}
        account = self.account_id or self.credentials.account_id
        if account: result["ChatGPT-Account-Id"] = account
        result.update(supplied); return result

    async def request(self, method: str, path: str, *, params: Mapping[str,Any] | None = None,
                      headers: Mapping[str,str] | None = None, json: Any = None) -> Response:
        if self._session is None: raise ConfigurationError("use 'async with CodexCloudClient()' before making requests")
        await self.authenticate()
        if not self.no_refresh and self._needs_refresh(): await self.refresh()
        url, method = _url(self.base_url,path,params), method.upper()
        async def send():
            kwargs: dict[str,Any] = {"headers":self._headers(headers),"allow_redirects":False}
            if json is not None: kwargs["json"] = json
            try:
                async with self._session.request(method,url,**kwargs) as raw:
                    return Response(raw.status,dict(raw.headers),await raw.read(),method,url)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc: raise NetworkError(f"{method} request failed") from exc
        response = await send()
        if response.status == 401 and not self.no_refresh:
            if not self._reload(): await self.refresh()
            response = await send()
        return response

    async def get(self,path: str,**kwargs): return await self.request("GET",path,**kwargs)
    async def post(self,path: str,**kwargs): return await self.request("POST",path,**kwargs)
    async def put(self,path: str,**kwargs): return await self.request("PUT",path,**kwargs)
    async def patch(self,path: str,**kwargs): return await self.request("PATCH",path,**kwargs)
    async def delete(self,path: str,**kwargs): return await self.request("DELETE",path,**kwargs)
