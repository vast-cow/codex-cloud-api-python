import asyncio, random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from .exceptions import EnvironmentNotFound, PollingTimeout, UnsupportedCapability
from .models import CreatedTask
from .parsing import parse_artifacts, parse_environments, parse_task, parse_tasks
from .routes import RouteAdapter
from .transport import AiohttpTransport

@dataclass(frozen=True)
class Capabilities: archive_tasks: bool = False

class CodexCloudClient:
    def __init__(self, *, auth=None, transport=None, branch_provider=None, archive_provider=None,
                 routes=None, base_url="https://chatgpt.com/backend-api", allow_custom_origin=False):
        if transport is None and auth is None: raise TypeError("auth or transport is required")
        self.transport = transport or AiohttpTransport(base_url, auth=auth, allow_custom_origin=allow_custom_origin)
        self.routes, self.branch_provider, self.archive_provider = routes or RouteAdapter(), branch_provider, archive_provider
    @property
    def capabilities(self): return Capabilities(bool(self.archive_provider and getattr(self.archive_provider, "supported", True)))
    async def __aenter__(self): return self
    async def __aexit__(self, *_): await self.close()
    async def close(self):
        await self.transport.close()
        if self.branch_provider and hasattr(self.branch_provider, "close"): await self.branch_provider.close()
    async def list_environments(self, *, repository=None):
        path = self.routes.environments_by_repository(repository) if repository else self.routes.environments()
        return parse_environments(await self.transport.request_json("GET", path))
    async def _environment(self, environment_id):
        for env in await self.list_environments():
            if env.id == environment_id: return env
        raise EnvironmentNotFound(f"Environment not found: {environment_id}")
    async def list_branches(self, environment_id):
        if self.branch_provider is None: raise UnsupportedCapability("Branch discovery provider is not configured")
        return await self.branch_provider.list_branches(await self._environment(environment_id))
    async def list_tasks(self, *, environment_id=None, limit=50) -> AsyncIterator:
        remaining, cursor = max(0, limit), None
        while remaining:
            params = {"limit": str(min(remaining, 100))}
            if environment_id: params["environment_id"] = environment_id
            if cursor: params["cursor"] = cursor
            data = await self.transport.request_json("GET", self.routes.task_list(), params=params)
            tasks = parse_tasks(data)
            for task in tasks[:remaining]: yield task
            remaining -= len(tasks)
            cursor = data.get("next_cursor") if isinstance(data, dict) else None
            if not cursor or not tasks: break
    async def create_task(self, *, environment_id, prompt, branch, qa_mode=False, attempts=1):
        if not environment_id or not prompt.strip() or not branch.strip(): raise ValueError("environment_id, prompt, and branch are required")
        if attempts < 1: raise ValueError("attempts must be positive")
        body = {"new_task": {"environment_id": environment_id, "branch": branch,
                "run_environment_in_qa_mode": qa_mode}, "input_items": [{"type":"message", "role":"user",
                "content":[{"content_type":"text", "text":prompt}]}]}
        if attempts > 1: body["metadata"] = {"best_of_n": attempts}
        raw = await self.transport.request_json("POST", self.routes.tasks(), json_body=body, retryable=False)
        task_id = raw.get("id") or raw.get("task_id") or (raw.get("task") or {}).get("id")
        if not task_id: from .exceptions import SchemaDriftError; raise SchemaDriftError("Created task response has no id")
        return CreatedTask(str(task_id), raw)
    async def get_task(self, task_id): return parse_task(await self.transport.request_json("GET", self.routes.task(task_id)))
    async def get_task_artifacts(self, task_id): return parse_artifacts(await self.transport.request_json("GET", self.routes.task(task_id)))
    async def list_attempts(self, task_id, turn_id): return parse_tasks(await self.transport.request_json("GET", self.routes.attempts(task_id, turn_id)))
    async def wait_for_task(self, task_id, *, timeout=None, minimum_interval=2.0, maximum_interval=15.0, progress=None):
        seconds = timeout.total_seconds() if isinstance(timeout, timedelta) else timeout
        async def poll():
            delay, previous = minimum_interval, None
            while True:
                task = await self.get_task(task_id)
                if progress: result = progress(task); await result if hasattr(result, "__await__") else None
                if task.status.terminal: return task
                delay = minimum_interval if previous is not None and task.status != previous else min(maximum_interval, delay * 1.5)
                previous = task.status; await asyncio.sleep(delay + random.random() * min(.5, delay / 4))
        try:
            return await asyncio.wait_for(poll(), seconds) if seconds is not None else await poll()
        except TimeoutError as exc: raise PollingTimeout(f"Timed out waiting for task {task_id}") from exc
    async def archive_task(self, task_id):
        if not self.capabilities.archive_tasks: raise UnsupportedCapability("Task archival is not configured for this backend")
        await self.archive_provider.archive(task_id)
