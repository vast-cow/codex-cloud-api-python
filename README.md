# Codex Cloud API for Python

An asynchronous, reusable Python client for Codex/ChatGPT backend APIs. It manages Device Code login, a private credential file, OAuth refresh and authenticated requests; the included CLI is only an adapter over this API.

> [!WARNING]
> This unofficial project uses undocumented Codex CLI and ChatGPT backend implementation details. These endpoints are **not** a stable public OpenAI API contract and can change without notice.

## Installation

Requires Python 3.10 or newer:

```bash
pip install .
```

## Python API

### Basic request

```python
import asyncio
from codex_cloud_api import CodexCloudClient

async def main():
    async with CodexCloudClient() as client:
        response = await client.get("/wham/environments")
        response.raise_for_status()
        print(response.json())

asyncio.run(main())
```

On first use, authentication is automatic. Applications should provide a callback to display the one-time Device Code:

```python
def show_code(code):
    print(f"Open {code.verification_url} and enter {code.user_code}")

async with CodexCloudClient(on_user_code=show_code) as client:
    response = await client.get("/wham/environments")
```

### Query parameters

```python
response = await client.get("/wham/tasks/list", params={"limit": 20})
```

Sequences are encoded as repeated query fields. Put query values in `params`, not in the path.

### POST with JSON

```python
response = await client.post("/wham/tasks", json=payload)
```

`get`, `post`, `put`, `patch`, and `delete` are conveniences over `request(method, path, ...)`. All accept normal header mappings; authentication-sensitive `Authorization`, `ChatGPT-Account-Id`, `Host`, and `Cookie` headers cannot be overridden.

### Custom credential file

```python
from pathlib import Path

async with CodexCloudClient(
    credential_file=Path("/private/app/codex-credentials.json")
) as client:
    response = await client.get("/wham/environments")
```

The default is `~/.codex-cloud-api/credentials.json`; `CODEX_CLOUD_API_CREDENTIALS` can override it. Files/directories are owner-only on POSIX.

### Explicit login

```python
from codex_cloud_api import login

credentials = await login(
    "/private/app/codex-credentials.json",
    on_user_code=show_code,
)
```

Use `load_credentials(path)` to load without logging in, or `acquire_credentials(path)` to load and automatically log in when missing.

### Configuration and responses

`CodexCloudClient` options include `credential_file`, `base_url`, `account_id`, `timeout`, `no_refresh`, and `on_user_code`. The base URL must use HTTPS on a trusted ChatGPT host. API targets must be paths, redirects are never followed, and OAuth refresh always uses the fixed OpenAI OAuth endpoint.

`Response` exposes `status`, `headers`, `body`, `method`, `url`, `ok`, `text()`, `json()`, and `raise_for_status()`.

The public API exported at package level is:

* Clients/models: `CodexCloudClient`, `Response`, `Credentials`, `DeviceCode`, `JsonCredentialStore`.
* Authentication: `load_credentials`, `acquire_credentials`, `device_code_login`, `login`.
* Errors: `CodexCloudError`, `ConfigurationError`, `UnsafeDestinationError`, `AuthenticationError`, `TokenRefreshError`, `NetworkError`, `HTTPResponseError`.

The client proactively refreshes near-expiry tokens, persists rotated refresh tokens, and retries one time after a 401. On a 401 it first reloads credentials written by another process; it refuses to silently switch accounts/workspaces. `no_refresh=True` disables proactive refresh and 401 authentication retry.

Library code never prints and does not include access tokens, refresh tokens, authorization codes, or PKCE secrets in exceptions. The Device Code callback receives only the public one-time code and verification URL.

## CLI

The console command uses `CodexCloudClient`; it has no separate request or authentication implementation.

```bash
codex-cloud-api GET /wham/environments
codex-cloud-api GET /wham/tasks/list --param limit=20
codex-cloud-api POST /wham/tasks --json @task.json
codex-cloud-api --login
```

Useful options include `--credential-file`, `--account-id`, `--base-url`, `--timeout`, `--header 'Name: value'`, `--no-refresh`, and `--raw`. Run `codex-cloud-api --help` for the complete interface.

## Security

Credentials represent an authenticated ChatGPT session. Never commit or log them. The library rejects arbitrary hosts and absolute request URLs, disables redirects for API/login/refresh calls, refreshes only at `https://auth.openai.com/oauth/token`, preserves refresh-token rotation, and rejects an account change detected while reloading credentials.

Use this only with accounts and resources you are authorized to access.
