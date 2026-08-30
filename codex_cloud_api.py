#!/usr/bin/env python3
"""
Call Codex/ChatGPT backend APIs using credentials already stored by Codex CLI.

Examples:
  python codex_cloud_api.py GET /wham/environments
  python codex_cloud_api.py GET /wham/tasks/list --query limit=20
  python codex_cloud_api.py POST /wham/tasks --json @task.json
  python codex_cloud_api.py GET /backend-api/wham/environments

Dependencies:
  pip install aiohttp

Optional (only for the legacy/direct OS-keyring store):
  pip install keyring

Security properties:
  * Never prints access/refresh tokens.
  * Refuses to send saved ChatGPT credentials to untrusted hosts.
  * Does not follow redirects.
  * Refreshes OAuth tokens against https://auth.openai.com/oauth/token only.

This relies on Codex CLI implementation details, not a stable public API contract.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

import aiohttp


DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"
REFRESH_TOKEN_URL = "https://auth.openai.com/oauth/token"
# Current public OAuth client id used by Codex CLI. May change upstream.
DEFAULT_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

KEYRING_SERVICE = "Codex Auth"
TRUSTED_CHATGPT_HOSTS = {
    "chatgpt.com",
    "chat.openai.com",
    "chatgpt-staging.com",
}

PROACTIVE_REFRESH_WINDOW = timedelta(minutes=5)
FALLBACK_REFRESH_AGE = timedelta(days=8)


class CodexApiError(RuntimeError):
    pass


class AuthError(CodexApiError):
    pass


class RefreshError(AuthError):
    pass


class Store(Protocol):
    description: str

    def load(self) -> dict[str, Any] | None:
        ...

    def save(self, value: dict[str, Any]) -> None:
        ...


class AuthJsonStore:
    def __init__(self, path: Path):
        self.path = path
        self.description = str(path)

    def load(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as exc:
            raise AuthError(f"invalid JSON in {self.path}: {exc}") from exc

    def save(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"

        # Atomic replace, with 0600 permissions on POSIX.
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        temp_path = Path(temp_name)
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.path)
            if os.name == "posix":
                os.chmod(self.path, 0o600)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            finally:
                raise


class DirectKeyringStore:
    """Compatibility with Codex's direct keyring backend.

    Newer Codex builds can also use an encrypted `secrets/codex_auth.age` store.
    That format is intentionally not reimplemented here.
    """

    def __init__(self, codex_home: Path):
        self.codex_home = codex_home
        canonical = codex_home.resolve(strict=False)
        digest = hashlib.sha256(str(canonical).encode()).hexdigest()[:16]
        self.account = f"cli|{digest}"
        self.description = f"OS keyring service={KEYRING_SERVICE!r} account={self.account!r}"

    @staticmethod
    def _keyring():
        try:
            import keyring  # type: ignore
        except ImportError as exc:
            raise AuthError(
                "Python package 'keyring' is required for --credential-source keyring; "
                "install it with: pip install keyring"
            ) from exc
        return keyring

    def load(self) -> dict[str, Any] | None:
        keyring = self._keyring()
        try:
            serialized = keyring.get_password(KEYRING_SERVICE, self.account)
        except Exception as exc:
            raise AuthError(f"failed to read Codex credential from OS keyring: {exc}") from exc
        if serialized is None:
            return None
        try:
            return json.loads(serialized)
        except json.JSONDecodeError as exc:
            raise AuthError("Codex keyring credential is not valid JSON") from exc

    def save(self, value: dict[str, Any]) -> None:
        keyring = self._keyring()
        serialized = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        try:
            keyring.set_password(KEYRING_SERVICE, self.account, serialized)
        except Exception as exc:
            raise AuthError(f"failed to update Codex credential in OS keyring: {exc}") from exc


@dataclass
class Credentials:
    access_token: str
    refresh_token: str | None
    account_id: str | None
    auth_document: dict[str, Any]
    store: Store


def b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def jwt_claims(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        value = json.loads(b64url_decode(parts[1]))
    except (ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def jwt_expiration(token: str) -> datetime | None:
    claims = jwt_claims(token)
    if not claims:
        return None
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def account_id_from_id_token(id_token: Any) -> str | None:
    if not isinstance(id_token, str) or not id_token:
        return None
    claims = jwt_claims(id_token)
    if not claims:
        return None
    auth_claims = claims.get("https://api.openai.com/auth")
    if isinstance(auth_claims, dict):
        account_id = auth_claims.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id:
            return account_id
    return None


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        # Rust chrono serializes UTC using RFC3339; support a trailing Z.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def extract_credentials(doc: dict[str, Any], store: Store) -> Credentials:
    tokens = doc.get("tokens")
    if not isinstance(tokens, dict):
        mode = doc.get("auth_mode")
        if doc.get("OPENAI_API_KEY"):
            raise AuthError(
                f"{store.description} contains API-key auth ({mode!r}), not ChatGPT OAuth tokens. "
                "Run `codex login` with ChatGPT authentication for backend-api/wham calls."
            )
        if doc.get("personal_access_token") or doc.get("agent_identity"):
            raise AuthError(
                f"{store.description} uses a non-OAuth Codex auth mode that this script does not impersonate."
            )
        raise AuthError(f"no ChatGPT OAuth tokens found in {store.description}")

    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise AuthError(f"access_token is missing in {store.description}")

    refresh_token = tokens.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        refresh_token = None

    account_id = tokens.get("account_id")
    if not isinstance(account_id, str) or not account_id:
        account_id = account_id_from_id_token(tokens.get("id_token"))

    return Credentials(
        access_token=access_token,
        refresh_token=refresh_token,
        account_id=account_id,
        auth_document=doc,
        store=store,
    )


def load_from_store(store: Store) -> Credentials | None:
    doc = store.load()
    if doc is None:
        return None
    return extract_credentials(doc, store)


def choose_store(args: argparse.Namespace) -> Credentials:
    codex_home = Path(args.codex_home).expanduser()
    auth_file = Path(args.auth_file).expanduser() if args.auth_file else codex_home / "auth.json"

    if args.credential_source == "file":
        creds = load_from_store(AuthJsonStore(auth_file))
        if creds is None:
            raise AuthError(f"Codex auth file not found: {auth_file}")
        return creds

    if args.credential_source == "keyring":
        creds = load_from_store(DirectKeyringStore(codex_home))
        if creds is None:
            raise AuthError("no Codex credential found in the direct OS keyring store")
        return creds

    # Match Codex AutoAuthStorage ordering as closely as practical: keyring, then file.
    try:
        creds = load_from_store(DirectKeyringStore(codex_home))
        if creds is not None:
            return creds
    except AuthError as exc:
        if args.verbose:
            print(f"note: direct keyring unavailable: {exc}", file=sys.stderr)

    creds = load_from_store(AuthJsonStore(auth_file))
    if creds is not None:
        return creds

    encrypted = codex_home / "secrets" / "codex_auth.age"
    if encrypted.exists():
        raise AuthError(
            f"Codex credentials appear to be in the encrypted store {encrypted}. "
            "This script supports auth.json and the legacy/direct keyring layout, but deliberately "
            "does not duplicate Codex's age-encrypted secrets implementation. Configure Codex to use "
            "file credential storage and sign in again, or extend Store for that backend."
        )

    raise AuthError(
        f"no usable Codex ChatGPT credentials found under {codex_home}; run `codex login` first"
    )


def validate_base_url(value: str) -> str:
    parts = urlsplit(value)
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "https"
        or host not in TRUSTED_CHATGPT_HOSTS
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or (parts.port not in (None, 443))
    ):
        raise CodexApiError(
            "refusing to send saved Codex credentials to an untrusted URL. "
            "Use https://chatgpt.com, https://chat.openai.com, or the trusted staging host on port 443."
        )

    path = parts.path.rstrip("/")
    if not path:
        path = "/backend-api"
    elif host in {"chatgpt.com", "chat.openai.com"} and path not in {
        "/backend-api",
        "/api/codex",
    }:
        # A custom path is allowed, but don't silently rewrite it.
        pass

    return urlunsplit(("https", parts.netloc, path, "", ""))


def build_api_url(base_url: str, path: str, query: list[str]) -> str:
    parsed_path = urlsplit(path)
    if parsed_path.scheme or parsed_path.netloc:
        raise CodexApiError("API target must be a path, not an absolute URL")
    if parsed_path.query or parsed_path.fragment:
        raise CodexApiError("put query parameters in --query; fragments are not supported")

    base = urlsplit(base_url)
    request_path = parsed_path.path
    if not request_path.startswith("/"):
        request_path = "/" + request_path

    # If the caller supplies a fully rooted backend path, don't duplicate base path.
    if request_path == "/backend-api" or request_path.startswith("/backend-api/"):
        final_path = request_path
    elif request_path == "/api/codex" or request_path.startswith("/api/codex/"):
        final_path = request_path
    else:
        final_path = base.path.rstrip("/") + request_path

    pairs: list[tuple[str, str]] = []
    for item in query:
        if "=" not in item:
            raise CodexApiError(f"invalid --query value {item!r}; expected NAME=VALUE")
        key, value = item.split("=", 1)
        pairs.append((key, value))

    return urlunsplit((base.scheme, base.netloc, final_path, urlencode(pairs), ""))


def parse_extra_headers(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    reserved = {"authorization", "chatgpt-account-id", "host", "cookie"}
    for item in values:
        if ":" not in item:
            raise CodexApiError(f"invalid --header value {item!r}; expected 'Name: Value'")
        name, value = item.split(":", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            raise CodexApiError("header name cannot be empty")
        if name.lower() in reserved:
            raise CodexApiError(f"header {name!r} is managed by the script and cannot be overridden")
        result[name] = value
    return result


def read_json_argument(value: str | None) -> Any | None:
    if value is None:
        return None
    if value == "-":
        text = sys.stdin.read()
    elif value.startswith("@"):
        text = Path(value[1:]).expanduser().read_text(encoding="utf-8")
    else:
        text = value
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CodexApiError(f"invalid JSON request body: {exc}") from exc


def should_refresh(creds: Credentials) -> bool:
    expires_at = jwt_expiration(creds.access_token)
    now = datetime.now(timezone.utc)
    if expires_at is not None:
        return expires_at <= now + PROACTIVE_REFRESH_WINDOW

    last_refresh = parse_iso_datetime(creds.auth_document.get("last_refresh"))
    return last_refresh is not None and last_refresh < now - FALLBACK_REFRESH_AGE


def utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CodexAuthSession:
    def __init__(
        self,
        creds: Credentials,
        *,
        timeout: float,
        account_id_override: str | None = None,
        verbose: bool = False,
    ):
        self.creds = creds
        self.timeout = timeout
        self.account_id_override = account_id_override
        self.verbose = verbose
        self._refresh_lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "CodexAuthSession":
        timeout = aiohttp.ClientTimeout(total=self.timeout, connect=min(10.0, self.timeout))
        self._session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            await self._session.close()

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("session is not open")
        return self._session

    def headers(self, extra: Mapping[str, str]) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.creds.access_token}",
            "Accept": "application/json",
            "User-Agent": "codex-auth-api/0.1",
        }
        account_id = self.account_id_override or self.creds.account_id
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        headers.update(extra)
        return headers

    def reload_credentials(self) -> bool:
        latest = load_from_store(self.creds.store)
        if latest is None:
            return False
        changed = latest.access_token != self.creds.access_token
        # Do not silently switch workspaces/accounts while recovering auth.
        old_account = self.account_id_override or self.creds.account_id
        new_account = self.account_id_override or latest.account_id
        if old_account and new_account and old_account != new_account:
            raise AuthError("Codex stored credentials changed to a different ChatGPT account/workspace")
        self.creds = latest
        return changed

    async def refresh(self) -> None:
        async with self._refresh_lock:
            # Another process may have refreshed since we loaded the file/keyring.
            if self.reload_credentials():
                if self.verbose:
                    print("auth: reloaded newer token from Codex credential store", file=sys.stderr)
                return

            refresh_token = self.creds.refresh_token
            if not refresh_token:
                raise RefreshError(
                    "this Codex credential has no refresh_token; run `codex login` again or disable refresh"
                )

            client_id = os.environ.get("CODEX_APP_SERVER_LOGIN_CLIENT_ID", "").strip()
            if not client_id:
                client_id = DEFAULT_CODEX_CLIENT_ID

            if self.verbose:
                print("auth: refreshing ChatGPT OAuth token", file=sys.stderr)

            # Do not honor an arbitrary refresh URL override here: this value is a bearer-equivalent secret.
            async with self.session.post(
                REFRESH_TOKEN_URL,
                json={
                    "client_id": client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                allow_redirects=False,
            ) as response:
                text = await response.text()
                if response.status < 200 or response.status >= 300:
                    code = None
                    try:
                        error_obj = json.loads(text)
                        if isinstance(error_obj, dict):
                            error_value = error_obj.get("error")
                            if isinstance(error_value, dict):
                                code = error_value.get("code")
                            elif isinstance(error_value, str):
                                code = error_value
                            if code is None:
                                code = error_obj.get("code")
                    except json.JSONDecodeError:
                        pass
                    suffix = f" ({code})" if code else ""
                    raise RefreshError(f"OAuth token refresh failed: HTTP {response.status}{suffix}")
                try:
                    refreshed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise RefreshError("OAuth refresh returned non-JSON data") from exc

            if not isinstance(refreshed, dict):
                raise RefreshError("OAuth refresh returned an invalid response")

            # Reload before writing so unrelated fields changed by Codex are preserved.
            latest_doc = self.creds.store.load()
            if latest_doc is None:
                latest_doc = self.creds.auth_document
            tokens = latest_doc.setdefault("tokens", {})
            if not isinstance(tokens, dict):
                raise RefreshError("Codex credential store changed to an incompatible format")

            for key in ("id_token", "access_token", "refresh_token"):
                value = refreshed.get(key)
                if isinstance(value, str) and value:
                    tokens[key] = value

            latest_doc["last_refresh"] = utc_now_rfc3339()
            self.creds.store.save(latest_doc)
            self.creds = extract_credentials(latest_doc, self.creds.store)

    async def request(
        self,
        method: str,
        url: str,
        *,
        extra_headers: Mapping[str, str],
        json_body: Any | None,
        no_refresh: bool,
    ) -> tuple[int, Mapping[str, str], bytes]:
        if not no_refresh and should_refresh(self.creds):
            await self.refresh()

        async def send_once() -> tuple[int, Mapping[str, str], bytes]:
            kwargs: dict[str, Any] = {
                "headers": self.headers(extra_headers),
                "allow_redirects": False,
            }
            if json_body is not None:
                kwargs["json"] = json_body
            async with self.session.request(method, url, **kwargs) as response:
                body = await response.read()
                return response.status, dict(response.headers), body

        status, headers, body = await send_once()
        if status != 401 or no_refresh:
            return status, headers, body

        if self.verbose:
            print("auth: API returned 401; reloading Codex credential store", file=sys.stderr)
        if self.reload_credentials():
            return await send_once()

        await self.refresh()
        return await send_once()


def print_response(
    status: int,
    headers: Mapping[str, str],
    body: bytes,
    *,
    raw: bool,
    include_headers: bool,
) -> None:
    print(f"HTTP {status}", file=sys.stderr)
    if include_headers:
        for name, value in headers.items():
            print(f"{name}: {value}", file=sys.stderr)

    if raw:
        sys.stdout.buffer.write(body)
        if body and not body.endswith(b"\n"):
            sys.stdout.buffer.write(b"\n")
        return

    text = body.decode("utf-8", errors="replace")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        print(text)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Call Codex/ChatGPT backend APIs using Codex CLI credentials."
    )
    parser.add_argument("method", help="HTTP method, e.g. GET, POST, PATCH, DELETE")
    parser.add_argument("path", help="API path, e.g. /wham/environments")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"trusted ChatGPT API base (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--codex-home",
        default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
        help="Codex home directory (default: $CODEX_HOME or ~/.codex)",
    )
    parser.add_argument("--auth-file", help="explicit auth.json path")
    parser.add_argument(
        "--credential-source",
        choices=("auto", "file", "keyring"),
        default="auto",
        help="where to load Codex credentials from (default: auto)",
    )
    parser.add_argument(
        "--account-id",
        help="override ChatGPT-Account-Id for this request (does not modify stored auth)",
    )
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="query parameter; repeatable",
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="NAME:VALUE",
        help="extra request header; repeatable; auth headers cannot be overridden",
    )
    parser.add_argument(
        "--json",
        metavar="JSON|@FILE|-",
        help="JSON body as a string, @file, or '-' for stdin",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="total HTTP timeout in seconds")
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="never refresh the OAuth token; useful for read-only diagnostics",
    )
    parser.add_argument("--raw", action="store_true", help="write response body without JSON pretty-printing")
    parser.add_argument(
        "--include-headers", action="store_true", help="print response headers to stderr"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


async def async_main(args: argparse.Namespace) -> int:
    if args.timeout <= 0:
        raise CodexApiError("--timeout must be greater than zero")

    creds = choose_store(args)
    base_url = validate_base_url(args.base_url)
    url = build_api_url(base_url, args.path, args.query)
    extra_headers = parse_extra_headers(args.header)
    json_body = read_json_argument(args.json)

    if args.verbose:
        print(f"auth source: {creds.store.description}", file=sys.stderr)
        print(f"request: {args.method.upper()} {url}", file=sys.stderr)
        print(
            f"account header: {'present' if (args.account_id or creds.account_id) else 'absent'}",
            file=sys.stderr,
        )
        expires_at = jwt_expiration(creds.access_token)
        if expires_at:
            print(f"access token expires: {expires_at.isoformat()}", file=sys.stderr)

    async with CodexAuthSession(
        creds,
        timeout=args.timeout,
        account_id_override=args.account_id,
        verbose=args.verbose,
    ) as client:
        status, headers, body = await client.request(
            args.method.upper(),
            url,
            extra_headers=extra_headers,
            json_body=json_body,
            no_refresh=args.no_refresh,
        )

    print_response(
        status,
        headers,
        body,
        raw=args.raw,
        include_headers=args.include_headers,
    )
    return 0 if 200 <= status < 400 else 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        return 130
    except (CodexApiError, aiohttp.ClientError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
