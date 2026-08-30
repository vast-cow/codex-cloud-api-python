import asyncio, random
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
import aiohttp
from .exceptions import (AuthenticationError, AuthorizationError, BackendUnavailable,
                         CodexCloudError, RateLimitError, TaskNotFound)

class AiohttpTransport:
    RETRY_STATUSES = {429, 500, 502, 503, 504}
    def __init__(self, base_url="https://chatgpt.com/backend-api", *, auth, session=None,
                 allow_custom_origin=False, max_retries=3, timeout=None):
        parsed = urlparse(base_url)
        if not allow_custom_origin and (parsed.scheme != "https" or parsed.hostname not in {"chatgpt.com", "chat.openai.com"}):
            raise ValueError("Refusing to send credentials to an untrusted origin")
        self.base_url, self.auth, self._session = base_url.rstrip("/") + "/", auth, session
        self._owns_session, self.max_retries = session is None, max_retries
        self.timeout = timeout or aiohttp.ClientTimeout(total=60, connect=10, sock_read=45)
    async def __aenter__(self): return self
    async def __aexit__(self, *_): await self.close()
    async def close(self):
        if self._owns_session and self._session is not None: await self._session.close()
    async def _get_session(self):
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self.timeout,
                connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300))
        return self._session
    @staticmethod
    def _retry_after(value):
        if not value: return None
        try: return max(0.0, float(value))
        except ValueError:
            try: return max(0.0, (parsedate_to_datetime(value).timestamp() - __import__("time").time()))
            except (TypeError, ValueError): return None
    async def request_json(self, method, path, *, params=None, json_body=None, retryable=None):
        method = method.upper(); retryable = method in {"GET", "HEAD"} if retryable is None else retryable
        for attempt in range(self.max_retries + 1):
            headers = dict(await self.auth.headers())
            session = await self._get_session()
            async with session.request(method, urljoin(self.base_url, path.lstrip("/")), headers=headers,
                    params=params, json=json_body, allow_redirects=False) as response:
                request_id = response.headers.get("x-request-id")
                if 200 <= response.status < 300:
                    if response.status == 204: return {}
                    try: return await response.json()
                    except (aiohttp.ContentTypeError, ValueError) as exc:
                        raise CodexCloudError("Backend returned invalid JSON", status=response.status, request_id=request_id) from exc
                excerpt = (await response.text())[:500]
                retry_after = self._retry_after(response.headers.get("Retry-After"))
                if retryable and response.status in self.RETRY_STATUSES and attempt < self.max_retries:
                    await asyncio.sleep(retry_after if retry_after is not None else min(8, 0.5 * 2**attempt) + random.random() * .25)
                    continue
                kwargs = {"status": response.status, "request_id": request_id}
                message = f"Backend request failed ({response.status}): {excerpt}"
                if response.status == 401: raise AuthenticationError(message, **kwargs)
                if response.status == 403: raise AuthorizationError(message, **kwargs)
                if response.status == 404: raise TaskNotFound(message, **kwargs)
                if response.status == 429: raise RateLimitError(message, retry_after=retry_after, **kwargs)
                if response.status in {500,502,503,504}: raise BackendUnavailable(message, **kwargs)
                raise CodexCloudError(message, **kwargs)
        raise AssertionError("unreachable")
