from datetime import datetime
from typing import Any
from .exceptions import SchemaDriftError
from .models import Environment, PullRequestInfo, Task, TaskArtifacts, TaskStatus

def _items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list): return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("items", "environments", "tasks", "data"):
            if isinstance(data.get(key), list): return [x for x in data[key] if isinstance(x, dict)]
    raise SchemaDriftError("Expected a list response")

def parse_environment(raw: dict[str, Any]) -> Environment:
    env_id = raw.get("id") or raw.get("environment_id")
    if not env_id: raise SchemaDriftError("Environment has no id")
    repo = raw.get("repository_full_name") or raw.get("repo_full_name")
    repository = raw.get("repository")
    if not repo and isinstance(repository, dict):
        repo = repository.get("full_name") or repository.get("name_with_owner")
    return Environment(str(env_id), raw.get("label") or raw.get("name"), repo, raw.get("is_pinned"), raw)

def parse_environments(data: Any) -> list[Environment]: return [parse_environment(x) for x in _items(data)]

def _datetime(value: Any) -> datetime | None:
    if not value: return None
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError: return None

def parse_task(raw: dict[str, Any]) -> Task:
    task_id = raw.get("id") or raw.get("task_id")
    if not task_id: raise SchemaDriftError("Task has no id")
    status_raw = str(raw.get("status") or raw.get("task_status") or "unknown").lower()
    aliases = {"in_progress": TaskStatus.PENDING, "queued": TaskStatus.PENDING,
               "completed": TaskStatus.READY, "failed": TaskStatus.ERROR}
    try: status = TaskStatus(status_raw)
    except ValueError: status = aliases.get(status_raw, TaskStatus.UNKNOWN)
    env = raw.get("environment") if isinstance(raw.get("environment"), dict) else {}
    stats = raw.get("diff_stats") if isinstance(raw.get("diff_stats"), dict) else {}
    return Task(str(task_id), raw.get("title"), status,
        raw.get("environment_id") or env.get("id"), raw.get("environment_label") or env.get("label"),
        _datetime(raw.get("updated_at")), raw.get("archived"),
        raw.get("files_changed", stats.get("files_changed")), raw.get("lines_added", stats.get("lines_added")),
        raw.get("lines_removed", stats.get("lines_removed")), status_raw, raw)

def parse_tasks(data: Any) -> list[Task]: return [parse_task(x) for x in _items(data)]

def _turns(raw):
    for key in ("current_diff_task_turn", "current_assistant_turn"):
        if isinstance(raw.get(key), dict): yield raw[key]

def parse_artifacts(raw: dict[str, Any]) -> TaskArtifacts:
    messages, diff = [], None
    for turn in _turns(raw):
        for item in turn.get("output_items") or []:
            if not isinstance(item, dict): continue
            if item.get("type") in {"message", "assistant_message"}:
                content = item.get("content")
                if isinstance(content, str): messages.append(content)
                elif isinstance(content, list):
                    messages.extend(str(c.get("text")) for c in content if isinstance(c, dict) and c.get("text"))
            if diff is None and item.get("type") == "output_diff": diff = item.get("diff")
            if diff is None and item.get("type") == "pr" and isinstance(item.get("output_diff"), dict):
                diff = item["output_diff"].get("diff")
    worklog = raw.get("worklog") or {}
    for item in worklog.get("messages") or []:
        if isinstance(item, dict) and item.get("role") == "assistant":
            text = item.get("text") or item.get("content")
            if isinstance(text, str): messages.append(text)
    prs = raw.get("external_pull_requests", raw.get("pull_requests", [])) or []
    parsed_prs = []
    for pr in prs:
        if isinstance(pr, dict):
            parsed_prs.append(PullRequestInfo(pr.get("number"), pr.get("url") or pr.get("html_url"),
                pr.get("title"), pr.get("body") or pr.get("description"), pr.get("state"),
                pr.get("base") if isinstance(pr.get("base"), str) else (pr.get("base") or {}).get("ref"),
                pr.get("head") if isinstance(pr.get("head"), str) else (pr.get("head") or {}).get("ref"), pr.get("diff")))
    return TaskArtifacts(list(dict.fromkeys(messages)), parsed_prs, diff, raw.get("error"), raw)
