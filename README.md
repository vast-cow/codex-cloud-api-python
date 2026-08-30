# codex-cloud

An asynchronous Python client for the source-derived Codex Cloud task API. The
library intentionally hides unstable backend routes behind a route adapter,
uses a separate provider for GitHub branch discovery, and keeps task archival
capability-gated until its wire contract is verified.

```python
from codex_cloud import CodexCloudClient, GitHubBranchProvider, StaticBearerAuth

async with CodexCloudClient(
    auth=StaticBearerAuth(access_token, account_id),
    branch_provider=GitHubBranchProvider(github_token),
) as client:
    environments = await client.list_environments(repository="owner/repo")
    created = await client.create_task(
        environment_id=environments[0].id,
        prompt="Add parser tests",
        branch="main",
    )
    task = await client.wait_for_task(created.id)
    artifacts = await client.get_task_artifacts(task.id)
```

The ChatGPT bearer token is not an OpenAI Platform API key. Never pass either
token on a command line or commit it. Custom origins are rejected by default.

## Development

```console
python -m pip install -e '.[test]'
pytest
```
