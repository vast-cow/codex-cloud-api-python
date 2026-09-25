"""Device Code authentication and OAuth helpers."""
from __future__ import annotations
import asyncio, base64, json, os, time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
import aiohttp
from .credentials import Credentials, JsonCredentialStore, credentials_from_document, load_credentials
from .exceptions import AuthenticationError, NetworkError

REFRESH_TOKEN_URL = "https://auth.openai.com/oauth/token"
DEVICE_USER_CODE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
DEVICE_VERIFICATION_URL = "https://auth.openai.com/codex/device"
DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
DEFAULT_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

@dataclass(frozen=True)
class DeviceCode:
    verification_url: str
    user_code: str

def _claims(token: Any) -> dict[str, Any] | None:
    if not isinstance(token, str) or len(token.split(".")) != 3: return None
    try:
        part = token.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return value if isinstance(value, dict) else None
    except (ValueError, json.JSONDecodeError): return None

def account_id_from_id_token(token: Any) -> str | None:
    claims = _claims(token) or {}; auth = claims.get("https://api.openai.com/auth")
    value = auth.get("chatgpt_account_id") if isinstance(auth, dict) else None
    return value if isinstance(value, str) and value else None

def access_token_expiration(token: str) -> datetime | None:
    exp = (_claims(token) or {}).get("exp")
    try: return datetime.fromtimestamp(exp, timezone.utc) if isinstance(exp, (int, float)) else None
    except (ValueError, OSError, OverflowError): return None

def oauth_client_id() -> str:
    return os.getenv("CODEX_APP_SERVER_LOGIN_CLIENT_ID", "").strip() or DEFAULT_CODEX_CLIENT_ID

def _required(value: Any, key: str, context: str) -> str:
    result = value.get(key) if isinstance(value, dict) else None
    if not isinstance(result, str) or not result:
        raise AuthenticationError(f"{context} response is missing {key}")
    return result

async def device_code_login(credential_file: str | Path | JsonCredentialStore | None = None, *,
    timeout: float = 60.0, login_timeout: float = 900.0,
    on_user_code: Callable[[DeviceCode], Any] | None = None,
    session: aiohttp.ClientSession | None = None, sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic) -> Credentials:
    """Perform Device Code login and persist the result; secrets never enter errors."""
    store = credential_file if isinstance(credential_file, JsonCredentialStore) else JsonCredentialStore(
        credential_file or os.getenv("CODEX_CLOUD_API_CREDENTIALS") or Path.home()/".codex-cloud-api"/"credentials.json")
    own = session is None
    if own: session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout))
    assert session is not None
    try:
        try:
            async with session.post(DEVICE_USER_CODE_URL, json={"client_id": oauth_client_id()}, headers={"Accept":"application/json"}, allow_redirects=False) as response:
                if not 200 <= response.status < 300: raise AuthenticationError(f"Device Code request failed: HTTP {response.status}")
                first = await response.json(content_type=None)
            device_id = _required(first, "device_auth_id", "Device Code")
            code = first.get("user_code") or first.get("usercode")
            if not isinstance(code, str) or not code: raise AuthenticationError("Device Code response is missing user_code")
            if on_user_code: on_user_code(DeviceCode(DEVICE_VERIFICATION_URL, code))
            try: interval = max(float(first.get("interval", 5)), .01)
            except (TypeError, ValueError): interval = 5
            deadline = monotonic() + login_timeout; authorized = None
            while monotonic() < deadline:
                async with session.post(DEVICE_TOKEN_URL, json={"device_auth_id":device_id,"user_code":code}, headers={"Accept":"application/json"}, allow_redirects=False) as response:
                    if response.status in (403, 404): authorized = None
                    elif not 200 <= response.status < 300: raise AuthenticationError(f"Device Code authorization failed: HTTP {response.status}")
                    else: authorized = await response.json(content_type=None)
                if authorized is not None: break
                await sleep(interval)
            if authorized is None: raise AuthenticationError("Device Code authentication timed out")
            authorization = _required(authorized,"authorization_code","Device Code authorization")
            verifier = _required(authorized,"code_verifier","Device Code authorization")
            _required(authorized,"code_challenge","Device Code authorization")
            async with session.post(REFRESH_TOKEN_URL, data={"grant_type":"authorization_code","client_id":oauth_client_id(),"code":authorization,"redirect_uri":DEVICE_REDIRECT_URI,"code_verifier":verifier}, headers={"Accept":"application/json","Content-Type":"application/x-www-form-urlencoded"}, allow_redirects=False) as response:
                if not 200 <= response.status < 300: raise AuthenticationError(f"OAuth code exchange failed: HTTP {response.status}")
                tokens = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise NetworkError("authentication network request failed") from exc
        id_token = _required(tokens,"id_token","OAuth token")
        document = {"auth_mode":"chatgpt","tokens":{"id_token":id_token,"access_token":_required(tokens,"access_token","OAuth token"),"refresh_token":_required(tokens,"refresh_token","OAuth token"),"account_id":account_id_from_id_token(id_token)},"last_refresh":datetime.now(timezone.utc).isoformat().replace("+00:00","Z")}
        store.save(document); return credentials_from_document(document, store)
    finally:
        if own: await session.close()

async def acquire_credentials(credential_file: str | Path | None = None, *, timeout: float = 60.0,
    on_user_code: Callable[[DeviceCode], Any] | None = None) -> Credentials:
    """Load credentials, automatically logging in if they are absent."""
    existing = load_credentials(credential_file)
    return existing if existing is not None else await device_code_login(credential_file, timeout=timeout, on_user_code=on_user_code)

async def login(credential_file: str | Path | None = None, **kwargs: Any) -> Credentials:
    """Explicitly start Device Code authentication, replacing saved credentials."""
    return await device_code_login(credential_file, **kwargs)
