import pytest

from codex_cloud import Branch, CreatedTask, Task, TaskArtifacts, TaskStatus
from examples.task_lifecycle import run_task_lifecycle


class ExampleClient:
    class Capabilities:
        archive_tasks = True

    capabilities = Capabilities()

    def __init__(self):
        self.archived = []

    async def list_environments(self):
        from codex_cloud import Environment

        return [Environment("env", "Example", "owner/repo")]

    async def list_branches(self, environment_id):
        assert environment_id == "env"
        return [Branch("main", "abc123")]

    async def create_task(self, **kwargs):
        assert kwargs == {"environment_id": "env", "branch": "main", "prompt": "Do it"}
        return CreatedTask("task")

    async def get_task(self, task_id):
        return Task(task_id, "Example", TaskStatus.PENDING)

    async def wait_for_task(self, task_id, **kwargs):
        kwargs["progress"](Task(task_id, "Example", TaskStatus.READY))
        return Task(task_id, "Example", TaskStatus.READY)

    async def get_task_artifacts(self, task_id):
        return TaskArtifacts(["Done"], [], "diff --git a/a b/a", None, {})

    async def archive_task(self, task_id):
        self.archived.append(task_id)


@pytest.mark.asyncio
async def test_complete_example_workflow(capsys):
    client = ExampleClient()
    await run_task_lifecycle(
        client, environment_id="env", branch="main", prompt="Do it"
    )

    assert client.archived == ["task"]
    output = capsys.readouterr().out
    assert "Current status: pending" in output
    assert "Summary 1:\nDone" in output
    assert "diff --git a/a b/a" in output
    assert "Archived task task" in output
