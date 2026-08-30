# Codex API CLI

A small Python utility for calling Codex / ChatGPT backend APIs using credentials already managed by the Codex CLI.

It reuses the ChatGPT OAuth credentials stored by `codex login`, automatically attaches the required authentication headers, refreshes expired access tokens when possible, and lets you issue arbitrary HTTP requests to Codex backend paths.

> [!WARNING]
> This project relies on implementation details of the Codex CLI and ChatGPT backend. The `/backend-api/wham/...` endpoints are not a stable public OpenAI API contract and may change without notice.

## Features

* Reuses credentials created by `codex login`
* Supports `~/.codex/auth.json`
* Supports the Codex direct OS keyring credential store
* Automatically sends:

  * `Authorization: Bearer <access_token>`
  * `ChatGPT-Account-Id: <account_id>` when available
* Proactively refreshes OAuth access tokens before expiration
* Retries once after an HTTP `401`
* Supports refresh-token rotation
* Writes refreshed credentials back to the original credential store
* Supports arbitrary HTTP methods:

  * `GET`
  * `POST`
  * `PUT`
  * `PATCH`
  * `DELETE`
  * and others
* Supports query parameters and custom headers
* Supports JSON request bodies from:

  * the command line
  * a file
  * stdin
* Pretty-prints JSON responses
* Can output raw response bodies
* Refuses to send stored Codex credentials to arbitrary hosts
* Does not follow HTTP redirects

## Requirements

* Python 3.10+
* An existing Codex CLI installation
* A ChatGPT-authenticated Codex session

Install the required Python dependency:

```bash
pip install aiohttp
```

If your Codex credentials are stored in the legacy/direct OS keyring, also install:

```bash
pip install keyring
```

## Authentication

First authenticate using the official Codex CLI:

```bash
codex login
```

This tool does not perform the interactive browser login flow itself. Instead, it reuses credentials already managed by Codex.

Depending on your Codex configuration, credentials may be stored in:

```text
~/.codex/auth.json
```

or in the operating system's credential/keyring store.

The expected OAuth credential structure contains values similar to:

```json
{
  "auth_mode": "chatgpt",
  "tokens": {
    "id_token": "...",
    "access_token": "...",
    "refresh_token": "...",
    "account_id": "..."
  },
  "last_refresh": "..."
}
```

The script never intentionally prints the access token or refresh token.

### Encrypted Codex credential storage

Recent Codex versions may use an encrypted credential store such as:

```text
~/.codex/secrets/codex_auth.age
```

This script deliberately does not reimplement Codex's encrypted secrets subsystem.

If your credentials exist only in that store, the tool will report that the credential format is unsupported.

## Basic usage

The general syntax is:

```text
python codex_api.py METHOD PATH [OPTIONS]
```

For example:

```bash
python codex_api.py GET /wham/environments
```

By default, requests are sent relative to:

```text
https://chatgpt.com/backend-api
```

Therefore:

```bash
python codex_api.py GET /wham/environments
```

requests:

```text
GET https://chatgpt.com/backend-api/wham/environments
```

## Examples

### List Codex environments

```bash
python codex_api.py GET /wham/environments
```

### List tasks

```bash
python codex_api.py GET /wham/tasks/list
```

### Add query parameters

Use `--query` once per parameter:

```bash
python codex_api.py GET /wham/tasks/list \
  --query limit=20
```

Multiple parameters can be supplied:

```bash
python codex_api.py GET /wham/tasks/list \
  --query limit=20 \
  --query cursor=abc123
```

### Retrieve a task

```bash
python codex_api.py GET /wham/tasks/TASK_ID
```

Example:

```bash
python codex_api.py GET /wham/tasks/task_123456
```

### Create a task

A task request can be supplied directly:

```bash
python codex_api.py POST /wham/tasks \
  --json '{
    "new_task": {
      "environment_id": "ENVIRONMENT_ID",
      "branch": "main",
      "run_environment_in_qa_mode": false
    },
    "input_items": [
      {
        "type": "message",
        "role": "user",
        "content": [
          {
            "content_type": "text",
            "text": "Improve the README."
          }
        ]
      }
    ]
  }'
```

### Read the JSON body from a file

Create `task.json`:

```json
{
  "new_task": {
    "environment_id": "ENVIRONMENT_ID",
    "branch": "main",
    "run_environment_in_qa_mode": false
  },
  "input_items": [
    {
      "type": "message",
      "role": "user",
      "content": [
        {
          "content_type": "text",
          "text": "Add unit tests for the parser."
        }
      ]
    }
  ]
}
```

Then run:

```bash
python codex_api.py POST /wham/tasks \
  --json @task.json
```

### Read the JSON body from stdin

```bash
cat task.json | python codex_api.py POST /wham/tasks --json -
```

This is also useful with dynamically generated JSON:

```bash
jq -n \
  --arg environment "$ENVIRONMENT_ID" \
  --arg prompt "Update the documentation." \
  '{
    new_task: {
      environment_id: $environment,
      branch: "main",
      run_environment_in_qa_mode: false
    },
    input_items: [
      {
        type: "message",
        role: "user",
        content: [
          {
            content_type: "text",
            text: $prompt
          }
        ]
      }
    ]
  }' |
python codex_api.py POST /wham/tasks --json -
```

## Response output

JSON responses are pretty-printed by default.

For example:

```bash
python codex_api.py GET /wham/environments
```

may produce:

```json
{
  "items": [
    {
      "id": "env_...",
      "label": "example-project"
    }
  ]
}
```

The HTTP status is written to stderr:

```text
HTTP 200
```

### Raw output

Use `--raw` to write the response body without JSON formatting:

```bash
python codex_api.py GET /wham/tasks/TASK_ID --raw
```

This can be useful when piping the result to another program:

```bash
python codex_api.py GET /wham/tasks/TASK_ID --raw | jq .
```

### Include response headers

Use:

```bash
python codex_api.py GET /wham/environments \
  --include-headers
```

Response headers are written to stderr.

## Custom request headers

Additional request headers can be supplied with `--header`:

```bash
python codex_api.py GET /wham/environments \
  --header 'Accept-Language: en-US'
```

The option can be repeated:

```bash
python codex_api.py GET /wham/environments \
  --header 'Accept-Language: en-US' \
  --header 'X-Custom-Header: example'
```

For security reasons, the following headers cannot be overridden:

```text
Authorization
ChatGPT-Account-Id
Host
Cookie
```

## Credential sources

The default mode is:

```text
--credential-source auto
```

In automatic mode, the script tries:

1. The Codex direct OS keyring store
2. `auth.json`

### Force `auth.json`

```bash
python codex_api.py GET /wham/environments \
  --credential-source file
```

The default file is:

```text
$CODEX_HOME/auth.json
```

or, if `CODEX_HOME` is not set:

```text
~/.codex/auth.json
```

### Use a specific auth file

```bash
python codex_api.py GET /wham/environments \
  --credential-source file \
  --auth-file /path/to/auth.json
```

### Force OS keyring

Install the optional dependency:

```bash
pip install keyring
```

Then:

```bash
python codex_api.py GET /wham/environments \
  --credential-source keyring
```

## Custom Codex home

By default, the tool uses:

```text
$CODEX_HOME
```

if set, otherwise:

```text
~/.codex
```

You can override it:

```bash
python codex_api.py GET /wham/environments \
  --codex-home ~/.codex-work
```

## ChatGPT account/workspace selection

When available, the script reads the ChatGPT account ID from the stored Codex credentials and sends:

```text
ChatGPT-Account-Id: ...
```

You can override it for a single request:

```bash
python codex_api.py GET /wham/environments \
  --account-id ACCOUNT_ID
```

This does not modify the stored Codex credentials.

## OAuth token refresh

The tool attempts to follow Codex's ChatGPT OAuth token-refresh behavior.

If the access token is a JWT, its `exp` claim is inspected locally.

The script refreshes the token when it is within approximately five minutes of expiration.

The refresh request is sent only to:

```text
https://auth.openai.com/oauth/token
```

using a request equivalent to:

```json
{
  "client_id": "...",
  "grant_type": "refresh_token",
  "refresh_token": "..."
}
```

If the response provides a new:

```text
access_token
refresh_token
id_token
```

those values are written back to the original credential store.

This is important because OAuth refresh-token rotation may replace the previous refresh token.

### HTTP 401 handling

When an API request returns `401`, the tool:

1. Reloads the Codex credential store in case another Codex process refreshed the token.
2. Retries with the newer token if one exists.
3. Otherwise performs an OAuth refresh.
4. Retries the API request once.

It does not continuously retry authentication failures.

### Disable token refresh

For diagnostics, token refresh can be disabled:

```bash
python codex_api.py GET /wham/environments \
  --no-refresh
```

In this mode, the currently stored access token is used as-is.

## Verbose mode

Use `-v` or `--verbose`:

```bash
python codex_api.py GET /wham/environments -v
```

Verbose output includes information such as:

```text
auth source: /home/user/.codex/auth.json
request: GET https://chatgpt.com/backend-api/wham/environments
account header: present
access token expires: ...
```

Tokens themselves are not printed.

## Timeout

The default total HTTP timeout is 60 seconds.

Override it with:

```bash
python codex_api.py GET /wham/environments \
  --timeout 120
```

The value is specified in seconds.

## Base URL

The default base URL is:

```text
https://chatgpt.com/backend-api
```

It can be changed using:

```bash
python codex_api.py GET /wham/environments \
  --base-url https://chatgpt.com/backend-api
```

Saved ChatGPT credentials are intentionally restricted to trusted ChatGPT hosts.

The script rejects arbitrary destinations such as:

```bash
python codex_api.py GET /some/path \
  --base-url https://example.com
```

This prevents accidentally sending bearer credentials to an untrusted server.

The API target itself must also be a path rather than an absolute URL.

This is rejected:

```bash
python codex_api.py GET https://example.com/api
```

Use:

```bash
python codex_api.py GET /wham/environments
```

instead.

## Fully rooted paths

The script accepts both paths relative to the configured API base:

```bash
python codex_api.py GET /wham/environments
```

and paths already rooted at a known backend prefix:

```bash
python codex_api.py GET /backend-api/wham/environments
```

Similarly, `/api/codex/...` paths are not prefixed with `/backend-api`.

## Useful Codex Cloud examples

The following endpoints are examples observed in Codex's current implementation. They are internal and may change.

### Environments

```bash
python codex_api.py GET /wham/environments
```

### Tasks

List tasks:

```bash
python codex_api.py GET /wham/tasks/list
```

Retrieve a task:

```bash
python codex_api.py GET /wham/tasks/TASK_ID
```

Create a task:

```bash
python codex_api.py POST /wham/tasks \
  --json @task.json
```

### Inspect a task with `jq`

```bash
python codex_api.py GET /wham/tasks/TASK_ID --raw |
jq .
```

Save it:

```bash
python codex_api.py GET /wham/tasks/TASK_ID --raw \
  > task.json
```

### Poll a task

A simple shell polling loop can be built around the generic API client:

```bash
TASK_ID="task_..."

while true; do
  python codex_api.py GET "/wham/tasks/$TASK_ID" --raw > task.json

  jq . task.json

  # Adjust this condition to the current backend response schema.
  STATUS=$(jq -r '.status // empty' task.json)

  case "$STATUS" in
    ready|applied|error)
      break
      ;;
  esac

  sleep 5
done
```

Because the backend schema is not a public stable contract, inspect the actual task response before relying on a particular status field.

## Exit codes

The script uses the following general exit behavior:

| Exit code | Meaning                                                        |
| --------- | -------------------------------------------------------------- |
| `0`       | HTTP response was successful (`2xx` or `3xx`)                  |
| `1`       | API returned an HTTP error such as `4xx` or `5xx`              |
| `2`       | Local configuration, authentication, request, or network error |
| `130`     | Interrupted with Ctrl+C                                        |

This makes it suitable for shell scripts:

```bash
if python codex_api.py GET /wham/environments; then
  echo "Request succeeded"
else
  echo "Request failed"
fi
```

## CLI reference

```text
usage: codex_api.py [-h]
                    [--base-url BASE_URL]
                    [--codex-home CODEX_HOME]
                    [--auth-file AUTH_FILE]
                    [--credential-source {auto,file,keyring}]
                    [--account-id ACCOUNT_ID]
                    [--query NAME=VALUE]
                    [--header NAME:VALUE]
                    [--json JSON|@FILE|-]
                    [--timeout TIMEOUT]
                    [--no-refresh]
                    [--raw]
                    [--include-headers]
                    [-v]
                    method
                    path
```

### Positional arguments

`method`

HTTP method to use.

Examples:

```text
GET
POST
PATCH
DELETE
```

`path`

Codex backend API path.

Example:

```text
/wham/environments
```

### Options

`--base-url URL`

Set the trusted ChatGPT backend base URL.

Default:

```text
https://chatgpt.com/backend-api
```

`--codex-home PATH`

Set the Codex home directory.

Default:

```text
$CODEX_HOME
```

or:

```text
~/.codex
```

`--auth-file PATH`

Use a specific `auth.json` file.

`--credential-source auto|file|keyring`

Select the credential source.

Default:

```text
auto
```

`--account-id ID`

Override `ChatGPT-Account-Id` for the current request.

`--query NAME=VALUE`

Add a query parameter. May be repeated.

`--header NAME:VALUE`

Add an HTTP request header. May be repeated.

Authentication-sensitive headers cannot be overridden.

`--json JSON|@FILE|-`

Provide a JSON request body.

Examples:

```bash
--json '{"foo":"bar"}'
--json @request.json
--json -
```

`--timeout SECONDS`

Set the total HTTP timeout.

Default:

```text
60
```

`--no-refresh`

Disable OAuth token refresh.

`--raw`

Write the response body without JSON pretty-printing.

`--include-headers`

Write HTTP response headers to stderr.

`-v`, `--verbose`

Enable diagnostic output.

## Security considerations

This tool handles credentials equivalent to an authenticated ChatGPT session. Treat it accordingly.

### Do not expose `auth.json`

Never:

* commit `~/.codex/auth.json` to Git
* paste its contents into issues
* log access or refresh tokens
* copy it to an untrusted machine

A suitable `.gitignore` entry is:

```gitignore
auth.json
*.token
```

### Credential destination restrictions

The tool intentionally validates the API base URL before adding Codex credentials.

It will not send stored credentials to arbitrary domains.

### Redirects

HTTP redirects are disabled.

This avoids forwarding authentication headers to an unexpected redirect destination.

### Refresh tokens

Refresh tokens should be treated as long-lived credentials.

If a refresh occurs and the authorization server rotates the refresh token, the script updates the credential store so that subsequent Codex CLI sessions can use the latest credential.

Running multiple independent programs that modify the same Codex credential store may still cause races. Avoid manipulating the underlying credential files manually while requests are running.

## Limitations

### Internal API

The most important limitation is that this tool is not using a documented public Codex Cloud REST API.

Routes such as:

```text
/backend-api/wham/tasks
/backend-api/wham/tasks/list
/backend-api/wham/environments
```

are based on the current Codex implementation and may be renamed, changed, or removed.

### Schema changes

Request and response JSON schemas may change independently of this tool.

The generic HTTP interface is intentional: most backend changes can be handled by changing the requested path or JSON payload without changing the authentication client.

### Encrypted credential store

The newer Codex encrypted credential store is not currently supported.

The script supports:

* `auth.json`
* the direct OS keyring representation

It does not directly decrypt:

```text
~/.codex/secrets/codex_auth.age
```

### Authentication modes

This tool is intended for Codex ChatGPT OAuth credentials.

It does not impersonate all other Codex authentication modes, including:

* API-key-only authentication
* Agent Identity authentication
* Personal access tokens
* other provider-specific credential mechanisms

## Why this exists

Codex CLI already contains the difficult parts of authentication:

* ChatGPT login
* OAuth token issuance
* account/workspace selection
* refresh-token handling
* credential persistence

This tool provides a thin asynchronous HTTP client on top of those existing credentials so that backend behavior can be inspected or automated without reimplementing the full Codex CLI.

The design intentionally keeps:

```text
Codex credential storage
        ↓
OAuth authentication
        ↓
aiohttp transport
        ↓
arbitrary Codex backend endpoint
```

separate from endpoint-specific application logic.

That makes it useful as a foundation for higher-level tools such as:

* Codex Cloud task clients
* environment browsers
* task status monitors
* diff retrieval tools
* automation scripts
* API exploration and debugging utilities

## Disclaimer

This project is unofficial.

It is not a replacement for supported OpenAI APIs and is not guaranteed to remain compatible with future Codex or ChatGPT backend changes.

Use it only with accounts and resources you are authorized to access.
