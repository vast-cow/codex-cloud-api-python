from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote

class RouteStyle(StrEnum): WHAM="wham"; CODEX="codex"
@dataclass(frozen=True)
class RouteAdapter:
    style: RouteStyle = RouteStyle.WHAM
    @property
    def prefix(self): return "/wham" if self.style == RouteStyle.WHAM else "/api/codex"
    def environments(self): return f"{self.prefix}/environments"
    def environments_by_repository(self, repository: str):
        owner, repo = repository.strip("/").split("/", 1)
        return f"{self.environments()}/by-repo/github/{quote(owner, safe='')}/{quote(repo, safe='')}"
    def tasks(self): return f"{self.prefix}/tasks"
    def task_list(self): return f"{self.tasks()}/list"
    def task(self, task_id: str): return f"{self.tasks()}/{quote(task_id, safe='')}"
    def attempts(self, task_id: str, turn_id: str):
        return f"{self.task(task_id)}/turns/{quote(turn_id, safe='')}/attempts"
