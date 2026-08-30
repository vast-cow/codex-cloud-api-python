"""End-to-end examples for the Codex Cloud task lifecycle.

The caller supplies a client because authentication and task archival are
backend-specific.  To run the complete example, configure the client with an
``archive_provider`` whose ``archive(task_id)`` coroutine implements the
verified archival contract for your backend.
"""

import asyncio

from codex_cloud import CodexCloudClient


async def list_available_environments(client: CodexCloudClient):
    """List every environment visible to the authenticated account."""
    environments = await client.list_environments()
    for environment in environments:
        repository = environment.repository_full_name or "(no repository)"
        print(f"{environment.id}: {environment.label or '(unnamed)'} [{repository}]")
    return environments


async def list_environment_branches(
    client: CodexCloudClient, environment_id: str
):
    """List branches for one environment via the configured branch provider."""
    branches = await client.list_branches(environment_id)
    for branch in branches:
        print(f"{branch.name}: {branch.sha or '(SHA unavailable)'}")
    return branches


async def create_and_wait_for_task(
    client: CodexCloudClient,
    *,
    environment_id: str,
    branch: str,
    prompt: str,
):
    """Create a task, inspect its current status, and wait for completion."""
    created = await client.create_task(
        environment_id=environment_id,
        branch=branch,
        prompt=prompt,
    )
    print(f"Created task {created.id}")

    current = await client.get_task(created.id)
    print(f"Current status: {current.status.value}")

    completed = await client.wait_for_task(
        created.id,
        timeout=30 * 60,
        progress=lambda task: print(f"Task status: {task.status.value}"),
    )
    return completed


async def print_task_artifacts(client: CodexCloudClient, task_id: str):
    """Retrieve and print summaries, pull-request messages, and the diff."""
    artifacts = await client.get_task_artifacts(task_id)

    for number, summary in enumerate(artifacts.assistant_messages, start=1):
        print(f"Summary {number}:\n{summary}")

    for pull_request in artifacts.pull_requests:
        print(f"PR: {pull_request.title or '(untitled)'}")
        print(pull_request.body or "(no PR message)")
        if pull_request.url:
            print(f"URL: {pull_request.url}")

    print(artifacts.unified_diff or "(no diff returned)")
    return artifacts


async def archive_task(client: CodexCloudClient, task_id: str) -> None:
    """Archive a task using the client's configured archival provider."""
    if not client.capabilities.archive_tasks:
        raise RuntimeError(
            "Configure CodexCloudClient(archive_provider=...) with your "
            "backend's verified archival implementation"
        )
    await client.archive_task(task_id)
    print(f"Archived task {task_id}")


async def run_task_lifecycle(
    client: CodexCloudClient,
    *,
    environment_id: str,
    branch: str,
    prompt: str,
) -> None:
    """Run all of the task lifecycle examples in sequence."""
    await list_available_environments(client)
    await list_environment_branches(client, environment_id)
    task = await create_and_wait_for_task(
        client,
        environment_id=environment_id,
        branch=branch,
        prompt=prompt,
    )
    await print_task_artifacts(client, task.id)
    await archive_task(client, task.id)


# Typical setup (an archival provider is intentionally application-supplied):
#
# from codex_cloud import GitHubBranchProvider, StaticBearerAuth
#
# async def main():
#     async with CodexCloudClient(
#         auth=StaticBearerAuth(CHATGPT_TOKEN, ACCOUNT_ID),
#         branch_provider=GitHubBranchProvider(GITHUB_TOKEN),
#         archive_provider=my_verified_archive_provider,
#     ) as client:
#         await run_task_lifecycle(
#             client,
#             environment_id="environment-id",
#             branch="main",
#             prompt="Add tests for the parser",
#         )
#
# asyncio.run(main())

