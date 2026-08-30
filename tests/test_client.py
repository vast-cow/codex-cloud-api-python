from datetime import timedelta
import pytest
from codex_cloud import CodexCloudClient, StaticBranchProvider, TaskStatus
from codex_cloud.exceptions import PollingTimeout, UnsupportedCapability

class Transport:
    def __init__(self, responses): self.responses=list(responses); self.calls=[]
    async def request_json(self, method, path, **kwargs):
        self.calls.append((method,path,kwargs)); return self.responses.pop(0)
    async def close(self): pass

@pytest.mark.asyncio
async def test_workflow_and_payload():
    transport=Transport([
        [{"id":"env", "repository":{"full_name":"owner/repo"}}],
        [{"id":"env", "repository":{"full_name":"owner/repo"}}],
        {"task_id":"task"},
        {"id":"task", "status":"completed"},
        {"id":"task", "current_diff_task_turn":{"output_items":[
            {"type":"message", "content":[{"text":"summary"}]},
            {"type":"output_diff", "diff":"diff --git a/a b/a"}]},
            "pull_requests":[{"number":1,"title":"PR","base":{"ref":"main"}}]},
    ])
    client=CodexCloudClient(transport=transport, branch_provider=StaticBranchProvider(["main"]))
    assert (await client.list_environments())[0].repository_full_name == "owner/repo"
    assert (await client.list_branches("env"))[0].name == "main"
    created=await client.create_task(environment_id="env", prompt="do it", branch="main", attempts=2)
    assert transport.calls[-1][2]["json_body"]["metadata"] == {"best_of_n":2}
    assert (await client.get_task(created.id)).status == TaskStatus.READY
    artifacts=await client.get_task_artifacts("task")
    assert artifacts.assistant_messages == ["summary"]
    assert artifacts.pull_requests[0].base == "main"

@pytest.mark.asyncio
async def test_archive_is_capability_gated():
    with pytest.raises(UnsupportedCapability):
        await CodexCloudClient(transport=Transport([])).archive_task("task")

@pytest.mark.asyncio
async def test_wait_timeout():
    client=CodexCloudClient(transport=Transport([{"id":"x","status":"pending"}]))
    with pytest.raises(PollingTimeout):
        await client.wait_for_task("x", timeout=timedelta(milliseconds=1), minimum_interval=.1)
