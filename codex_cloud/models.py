from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

class TaskStatus(StrEnum):
    PENDING="pending"; READY="ready"; APPLIED="applied"; ERROR="error"; UNKNOWN="unknown"
    @property
    def terminal(self) -> bool: return self in {self.READY, self.APPLIED, self.ERROR}

@dataclass(frozen=True)
class Environment:
    id: str; label: str | None = None; repository_full_name: str | None = None
    is_pinned: bool | None = None; raw: dict[str, Any] = field(default_factory=dict)
@dataclass(frozen=True)
class Branch:
    name: str; sha: str | None = None; protected: bool | None = None; source: str = "github"
@dataclass(frozen=True)
class Task:
    id: str; title: str | None; status: TaskStatus; environment_id: str | None = None
    environment_label: str | None = None; updated_at: datetime | None = None
    archived: bool | None = None; files_changed: int | None = None; lines_added: int | None = None
    lines_removed: int | None = None; raw_status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
@dataclass(frozen=True)
class CreatedTask:
    id: str; raw: dict[str, Any] = field(default_factory=dict)
@dataclass(frozen=True)
class PullRequestInfo:
    number: int | None = None; url: str | None = None; title: str | None = None
    body: str | None = None; state: str | None = None; base: str | None = None
    head: str | None = None; diff: str | None = None
@dataclass(frozen=True)
class TaskArtifacts:
    assistant_messages: list[str]; pull_requests: list[PullRequestInfo]
    unified_diff: str | None; error: str | None; raw: dict[str, Any]
