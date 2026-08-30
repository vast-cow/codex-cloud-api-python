from codex_cloud.models import TaskStatus
from codex_cloud.parsing import parse_artifacts, parse_environments, parse_task

def test_permissive_envelopes_and_unknown_status():
    assert parse_environments({"items":[{"environment_id":"e"}]})[0].id == "e"
    task=parse_task({"task_id":"t", "status":"brand_new"})
    assert task.status == TaskStatus.UNKNOWN and task.raw_status == "brand_new"

def test_worklog_fallback_and_nested_pr_diff():
    result=parse_artifacts({
        "current_assistant_turn":{"output_items":[{"type":"pr","output_diff":{"diff":"patch"}}]},
        "worklog":{"messages":[{"role":"assistant","text":"hello"},{"role":"user","text":"no"}]},
        "external_pull_requests":[{"html_url":"https://example.test/pr/1","body":"body"}],
    })
    assert result.unified_diff == "patch"
    assert result.assistant_messages == ["hello"]
    assert result.pull_requests[0].url.endswith("/1")
