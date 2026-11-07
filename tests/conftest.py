"""A nextasks that lives in memory, and the fixtures around it.

⚠️ **Invented people and lists.** This repository is public; a fixture is exactly
where a real household would end up on the internet without anybody meaning it.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nextasks.api import (
    Me,
    NextasksNotFoundError,
    Task,
    TaskList,
    TaskPage,
)
from custom_components.nextasks.const import CONF_TOKEN, CONF_URL, DOMAIN

pytest_plugins = "pytest_homeassistant_custom_component"

URL = "https://tasks.example.com"
TOKEN = "ntk_" + "t" * 40

FAMILY = "11111111-1111-4111-8111-111111111111"
HIDDEN_SPACE = "22222222-2222-4222-8222-222222222222"
SHOPPING = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CHORES = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SECRET = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{moment.microsecond // 1000:03d}Z"
    )


class FakeNextasks:
    """Answers like ``/api/v1`` does, as far as the integration asks."""

    def __init__(self) -> None:
        self.me: dict[str, Any] = {
            "name": "robin",
            "display_name": "Robin",
            "level": "write",
            "time_zone": "Europe/Berlin",
            "spaces": [{"id": FAMILY, "name": "Family"}],
            "version": "0.1.0",
            "features": ["tasks", "dashboard", "external_ref"],
        }
        self.lists: list[dict[str, Any]] = [
            self._list(SHOPPING, FAMILY, "Family", "Shopping"),
            self._list(CHORES, FAMILY, "Family", "Chores"),
            self._list(SECRET, HIDDEN_SPACE, "Attic", "Secret", hidden=True),
        ]
        self.tasks: dict[str, dict[str, Any]] = {}
        #: Ids gone for good, with when.
        self.gone: dict[str, datetime] = {}
        self.calls: list[tuple[str, Any]] = []
        self.counts = {
            "overdue": 1,
            "today": 2,
            "upcoming": 3,
            "assigned_to_me": 0,
            "flagged": 1,
            "open": 4,
        }
        self.fail: Exception | None = None

    @staticmethod
    def _list(
        list_id: str, space_id: str, space: str, name: str, *, hidden: bool = False
    ) -> dict[str, Any]:
        return {
            "id": list_id,
            "space_id": space_id,
            "space_name": space,
            "name": name,
            "color": "teal",
            "icon": "list",
            "writable": True,
            "hidden": hidden,
            "sections": [],
            "open": 0,
        }

    def add(self, title: str, list_id: str = SHOPPING, **fields: Any) -> dict[str, Any]:
        now = datetime.now(UTC)
        task = {
            "id": str(uuid.uuid4()),
            "list_id": list_id,
            "section_id": None,
            "parent_id": None,
            "title": title,
            "notes": "",
            "notes_text": "",
            "url": None,
            "due_date": None,
            "due_time": None,
            "repeat": None,
            "flagged": False,
            "priority": 0,
            "tags": [],
            "assignees": [],
            "completed": False,
            "completed_at": None,
            "completed_by": None,
            "trashed": False,
            "trashed_at": None,
            "subtasks": {"open": 0, "total": 0},
            "external_ref": None,
            "created_at": stamp(now),
            "updated_at": stamp(now),
            "link": f"{URL}/lists/{list_id}",
            "_changed": now,
        }
        task.update(fields)
        self.tasks[task["id"]] = task
        return task

    def change(self, task_id: str, **fields: Any) -> None:
        task = self.tasks[task_id]
        task.update(fields)
        task["_changed"] = datetime.now(UTC)

    def out(self, task: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in copy.deepcopy(task).items() if key != "_changed"}

    def client(self, session: Any, url: str, token: str) -> FakeClient:
        return FakeClient(self, url)


class FakeClient:
    def __init__(self, server: FakeNextasks, url: str) -> None:
        self.server = server
        self.url = url.rstrip("/")

    def _note(self, call: str, value: Any = None) -> None:
        self.server.calls.append((call, value))
        if self.server.fail is not None:
            raise self.server.fail

    async def me(self) -> Me:
        self._note("me")
        return Me.from_api(self.server.me)

    async def lists(self) -> list[TaskList]:
        self._note("lists")
        return [TaskList.from_api(item) for item in self.server.lists]

    async def tasks(self, **filters: str) -> TaskPage:
        self._note("tasks", dict(filters))
        since = filters.get("updated_since")
        moment = datetime.fromisoformat(since.replace("Z", "+00:00")) if since else None
        state = filters.get("state") or ("all" if moment else "open")
        found = []
        for task in self.server.tasks.values():
            if task["parent_id"] is not None:
                continue
            if moment is not None and task["_changed"] < moment:
                continue
            if state == "open" and task["completed"]:
                continue
            if state == "completed" and not task["completed"]:
                continue
            if task["trashed"] and moment is None:
                continue
            found.append(Task.from_api(self.server.out(task)))
        gone = [key for key, when in self.server.gone.items() if moment and when >= moment]
        return TaskPage(tasks=found, gone=gone, as_of=stamp(datetime.now(UTC)))

    async def dashboard(self) -> dict[str, Any]:
        self._note("dashboard")
        overdue = [self.server.out(task) for task in list(self.server.tasks.values())[:1]]
        return {"counts": dict(self.server.counts), "overdue": overdue, "today": [], "upcoming": []}

    async def create_task(self, body: dict[str, Any]) -> Task:
        self._note("create", body)
        fields = {key: value for key, value in body.items() if key != "list_id"}
        task = self.server.add(fields.pop("title"), body.get("list_id", SHOPPING), **fields)
        return Task.from_api(self.server.out(task))

    async def update_task(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self._note("update", (task_id, body))
        if task_id not in self.server.tasks:
            raise NextasksNotFoundError("Not there.", "not_found", 404)
        fields = dict(body)
        if fields.get("completed"):
            fields["completed_at"] = stamp(datetime.now(UTC))
        elif fields.get("completed") is False:
            fields["completed_at"] = None
        if fields.get("due_date", "") is None:
            fields["due_time"] = None
        self.server.change(task_id, **fields)
        return self.server.out(self.server.tasks[task_id])

    async def delete_task(self, task_id: str) -> None:
        self._note("delete", task_id)
        if task_id not in self.server.tasks:
            raise NextasksNotFoundError("Not there.", "not_found", 404)
        self.server.change(task_id, trashed=True, trashed_at=stamp(datetime.now(UTC)))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Without this, Home Assistant does not look in custom_components at all."""
    yield


@pytest.fixture
def server() -> Generator[FakeNextasks]:
    fake = FakeNextasks()
    with (
        patch("custom_components.nextasks.NextasksClient", fake.client),
        patch("custom_components.nextasks.config_flow.NextasksClient", fake.client),
    ):
        yield fake


@pytest.fixture
def entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="nextasks Family",
        unique_id=f"{URL}::robin::{FAMILY}",
        data={CONF_URL: URL, CONF_TOKEN: TOKEN},
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def days_ago(days: float) -> str:
    return stamp(datetime.now(UTC) - timedelta(days=days))
