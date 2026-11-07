"""A small client for nextasks' ``/api/v1``.

Kept free of Home Assistant on purpose: it takes an aiohttp session and speaks
the API, nothing more. The integration decides what an error means for the
user.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=20)
#: The API pages at most 500 tasks; fewer, larger pages keep a poll short.
PAGE_SIZE = 500
#: A safety stop: a server that keeps answering ``next_cursor`` must not keep
#: Home Assistant busy forever.
MAX_PAGES = 100


class NextasksError(Exception):
    """Anything that went wrong talking to nextasks."""

    def __init__(self, message: str, code: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class NextasksConnectionError(NextasksError):
    """nextasks could not be reached, or answered with a server error."""


class NextasksAuthError(NextasksError):
    """The token is missing, unknown, ran out or was blocked."""


class NextasksApiOffError(NextasksError):
    """The operator has not switched API tokens on."""


class NextasksRefusedError(NextasksError):
    """nextasks understood the request and said no (read-only token, no right)."""


class NextasksNotFoundError(NextasksError):
    """The task, list or space is not there, or not for this token."""


def _day(value: Any) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value))


def _clock(value: Any) -> time | None:
    if not value:
        return None
    return time.fromisoformat(str(value))


def _moment(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass(frozen=True, slots=True)
class Me:
    """Who the token speaks for."""

    name: str
    display_name: str
    level: str
    time_zone: str
    version: str
    features: tuple[str, ...]
    #: The spaces this token was given, as ``(id, name)``.
    spaces: tuple[tuple[str, str], ...] = ()

    @property
    def writes(self) -> bool:
        return self.level == "write"

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Me:
        return cls(
            name=str(raw["name"]),
            display_name=str(raw.get("display_name") or raw["name"]),
            level=str(raw.get("level") or "read"),
            time_zone=str(raw.get("time_zone") or "UTC"),
            version=str(raw.get("version") or ""),
            features=tuple(str(item) for item in raw.get("features") or ()),
            spaces=tuple(
                (str(item["id"]), str(item.get("name") or "")) for item in raw.get("spaces") or ()
            ),
        )


@dataclass(frozen=True, slots=True)
class TaskList:
    """A list, as ``GET /api/v1/lists`` names it."""

    id: str
    space_id: str
    space_name: str
    name: str
    writable: bool
    hidden: bool

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> TaskList:
        return cls(
            id=str(raw["id"]),
            space_id=str(raw["space_id"]),
            space_name=str(raw.get("space_name") or ""),
            name=str(raw.get("name") or ""),
            writable=bool(raw.get("writable")),
            hidden=bool(raw.get("hidden")),
        )


@dataclass(frozen=True, slots=True)
class Task:
    """A task, reduced to what Home Assistant can show."""

    id: str
    list_id: str
    parent_id: str | None
    title: str
    notes: str
    due_date: date | None
    due_time: time | None
    completed: bool
    completed_at: datetime | None
    trashed: bool
    subtasks_open: int
    subtasks_total: int
    link: str | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Task:
        subtasks = raw.get("subtasks") or {}
        return cls(
            id=str(raw["id"]),
            list_id=str(raw["list_id"]),
            parent_id=raw.get("parent_id"),
            title=str(raw.get("title") or ""),
            notes=str(raw.get("notes") or ""),
            due_date=_day(raw.get("due_date")),
            due_time=_clock(raw.get("due_time")),
            completed=bool(raw.get("completed")),
            completed_at=_moment(raw.get("completed_at")),
            trashed=bool(raw.get("trashed")),
            subtasks_open=int(subtasks.get("open") or 0),
            subtasks_total=int(subtasks.get("total") or 0),
            link=raw.get("link"),
        )


@dataclass(frozen=True, slots=True)
class TaskPage:
    """What a run through ``GET /api/v1/tasks`` found."""

    tasks: list[Task]
    gone: list[str]
    as_of: str


class NextasksClient:
    """Speaks ``/api/v1`` with one token."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self.url = url.rstrip("/")
        self._token = token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        try:
            async with self._session.request(
                method,
                f"{self.url}/api/v1{path}",
                params=params,
                json=body,
                headers=headers,
                timeout=TIMEOUT,
            ) as response:
                if response.status == 204:
                    return None
                try:
                    payload = await response.json(content_type=None)
                except aiohttp.ContentTypeError, ValueError:
                    payload = None
                if response.status >= 400:
                    raise _error(response.status, payload)
                return payload
        except NextasksError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise NextasksConnectionError(f"nextasks cannot be reached: {err}") from err

    async def me(self) -> Me:
        return Me.from_api(await self._request("GET", "/me"))

    async def lists(self) -> list[TaskList]:
        raw = await self._request("GET", "/lists")
        return [TaskList.from_api(item) for item in raw.get("lists") or ()]

    async def tasks(self, **filters: str) -> TaskPage:
        """All pages of ``GET /api/v1/tasks`` for these filters."""
        params = {**filters, "limit": str(PAGE_SIZE)}
        tasks: list[Task] = []
        gone: list[str] = []
        as_of = ""
        for _ in range(MAX_PAGES):
            raw = await self._request("GET", "/tasks", params=params)
            # The first page's time counts: what changed while paging comes again
            # with the next run, rather than not at all.
            as_of = as_of or str(raw.get("as_of") or "")
            for item in raw.get("tasks") or ():
                if item.get("gone"):
                    gone.append(str(item["id"]))
                else:
                    tasks.append(Task.from_api(item))
            cursor = raw.get("next_cursor")
            if not cursor:
                break
            params = {**params, "cursor": str(cursor)}
        return TaskPage(tasks=tasks, gone=gone, as_of=as_of)

    async def dashboard(self) -> dict[str, Any]:
        result: dict[str, Any] = await self._request("GET", "/dashboard")
        return result

    async def create_task(self, body: dict[str, Any]) -> Task:
        return Task.from_api(await self._request("POST", "/tasks", body=body))

    async def update_task(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """The task as it is afterwards, with ``next`` for a repeating one."""
        result: dict[str, Any] = await self._request("PATCH", f"/tasks/{task_id}", body=body)
        return result

    async def delete_task(self, task_id: str) -> None:
        await self._request("DELETE", f"/tasks/{task_id}")


def _error(status: int, payload: Any) -> NextasksError:
    detail = payload.get("detail") if isinstance(payload, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    message = detail.get("message") if isinstance(detail, dict) else None
    text = str(message or f"nextasks answered {status}")
    if status == 401 and code == "api_off":
        return NextasksApiOffError(text, code, status)
    if status == 401:
        return NextasksAuthError(text, code, status)
    if status == 404:
        return NextasksNotFoundError(text, code, status)
    if status == 429 or status >= 500:
        return NextasksConnectionError(text, code, status)
    if status in (403, 409, 413, 422):
        return NextasksRefusedError(text, code, status)
    return NextasksError(text, code, status)
