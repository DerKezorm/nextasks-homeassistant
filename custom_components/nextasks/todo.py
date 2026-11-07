"""Every nextasks list as a to-do list in Home Assistant."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from homeassistant.components.todo import TodoItem, TodoListEntity
from homeassistant.components.todo.const import TodoItemStatus, TodoListEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import NextasksError, NextasksNotFoundError, NextasksRefusedError, Task, TaskList
from .const import DOMAIN, OPT_EXCLUDED_LISTS
from .coordinator import NextasksConfigEntry, NextasksCoordinator
from .entity import list_id_from_unique_id, list_unique_id, space_device

PARALLEL_UPDATES = 0

WRITE_FEATURES = (
    TodoListEntityFeature.CREATE_TODO_ITEM
    | TodoListEntityFeature.UPDATE_TODO_ITEM
    | TodoListEntityFeature.DELETE_TODO_ITEM
    | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
    | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
    | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
)


def shown(entry: NextasksConfigEntry, task_list: TaskList) -> bool:
    """Lists of spaces the account hid stay hidden here too, as do switched-off ones."""
    excluded = entry.options.get(OPT_EXCLUDED_LISTS) or []
    return not task_list.hidden and task_list.id not in excluded


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NextasksConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    known: set[str] = set()

    @callback
    def _sync_lists() -> None:
        lists = coordinator.data.lists
        wanted = {key for key, value in lists.items() if shown(entry, value)}
        new = [key for key in lists if key in wanted and key not in known]
        if new:
            known.update(new)
            async_add_entities(NextasksTodoList(coordinator, key) for key in new)
        # A list that was deleted, moved out of the token's sight or switched
        # off goes away for good rather than lingering as "unavailable".
        for item in er.async_entries_for_config_entry(registry, entry.entry_id):
            if item.domain != "todo":
                continue
            list_id = list_id_from_unique_id(item.unique_id)
            if list_id is not None and list_id not in wanted:
                known.discard(list_id)
                registry.async_remove(item.entity_id)

    _sync_lists()
    entry.async_on_unload(coordinator.async_add_listener(_sync_lists))


def _subtasks_line(language: str, task: Task) -> str | None:
    if task.subtasks_total == 0:
        return None
    done = task.subtasks_total - task.subtasks_open
    if language.startswith("de"):
        return f"Unteraufgaben: {done} von {task.subtasks_total} erledigt"
    return f"Subtasks: {done} of {task.subtasks_total} done"


class NextasksTodoList(CoordinatorEntity[NextasksCoordinator], TodoListEntity):
    """One nextasks list."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: NextasksCoordinator, list_id: str) -> None:
        super().__init__(coordinator)
        task_list = coordinator.data.lists[list_id]
        self._list_id = list_id
        self._attr_unique_id = list_unique_id(coordinator, list_id)
        self._attr_device_info = space_device(coordinator, task_list)
        self._attr_name = task_list.name

    @property
    def _list(self) -> TaskList | None:
        return self.coordinator.data.lists.get(self._list_id)

    @property
    def available(self) -> bool:
        return super().available and self._list is not None

    @property
    def name(self) -> str | None:
        task_list = self._list
        return task_list.name if task_list is not None else self._attr_name

    @property
    def supported_features(self) -> TodoListEntityFeature:
        task_list = self._list
        if task_list is None or not task_list.writable or not self.coordinator.data.me.writes:
            return TodoListEntityFeature(0)
        return WRITE_FEATURES

    @property
    def _zone(self) -> tzinfo:
        return (
            dt_util.get_time_zone(self.coordinator.data.me.time_zone)
            or dt_util.get_default_time_zone()
        )

    # --- reading ------------------------------------------------------------

    def _due(self, task: Task) -> date | datetime | None:
        if task.due_date is None:
            return None
        if task.due_time is None:
            return task.due_date
        return datetime.combine(task.due_date, task.due_time, tzinfo=self._zone)

    def _description(self, task: Task) -> str | None:
        parts = [task.notes] if task.notes else []
        line = _subtasks_line(self.hass.config.language, task)
        if line:
            parts.append(line)
        return "\n\n".join(parts) or None

    def _item(self, task: Task) -> TodoItem:
        return TodoItem(
            uid=task.id,
            summary=task.title,
            status=TodoItemStatus.COMPLETED if task.completed else TodoItemStatus.NEEDS_ACTION,
            due=self._due(task),
            description=self._description(task),
        )

    @property
    def todo_items(self) -> list[TodoItem]:
        tasks = [
            task
            for task in self.coordinator.data.tasks.values()
            if task.list_id == self._list_id and task.parent_id is None and not task.trashed
        ]
        open_tasks = [task for task in tasks if not task.completed]
        days = self.coordinator.completed_days
        since = dt_util.utcnow() - timedelta(days=days)
        done = [
            task
            for task in tasks
            if task.completed
            and days > 0
            and (task.completed_at is None or task.completed_at >= since)
        ]
        done.sort(key=lambda task: task.completed_at or since, reverse=True)
        return [self._item(task) for task in (*open_tasks, *done)]

    # --- writing ------------------------------------------------------------

    def _due_fields(self, due: date | datetime | None) -> dict[str, Any]:
        if due is None:
            return {"due_date": None}
        if isinstance(due, datetime):
            moment = due if due.tzinfo else due.replace(tzinfo=dt_util.get_default_time_zone())
            local = moment.astimezone(self._zone)
            return {"due_date": local.date().isoformat(), "due_time": local.strftime("%H:%M")}
        return {"due_date": due.isoformat(), "due_time": None}

    def _notes(self, task: Task | None, description: str | None) -> str:
        """The notes to store, without the line about subtasks that was only shown."""
        text = description or ""
        line = _subtasks_line(self.hass.config.language, task) if task else None
        if line and text.endswith(line):
            text = text[: -len(line)].rstrip()
        return text

    async def async_create_todo_item(self, item: TodoItem) -> None:
        body: dict[str, Any] = {"list_id": self._list_id, "title": item.summary or ""}
        if item.description:
            body["notes"] = item.description
        if item.due is not None:
            body.update(self._due_fields(item.due))
            if body.get("due_time") is None:
                body.pop("due_time", None)
        task = await self._call(self.coordinator.client.create_task(body))
        if item.status == TodoItemStatus.COMPLETED:
            changed = await self._call(
                self.coordinator.client.update_task(task.id, {"completed": True})
            )
            task = Task.from_api(changed)
        self.coordinator.async_apply(task)
        await self.coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if item.uid is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="task_not_found"
            )
        current = self.coordinator.data.tasks.get(item.uid)
        if current is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="task_not_found",
            )
        body: dict[str, Any] = {}
        if item.summary is not None and item.summary != current.title:
            body["title"] = item.summary
        if item.status is not None:
            completed = item.status == TodoItemStatus.COMPLETED
            if completed != current.completed:
                body["completed"] = completed
        if item.due != self._due(current):
            due = self._due_fields(item.due)
            if due.get("due_date") != (current.due_date.isoformat() if current.due_date else None):
                body["due_date"] = due["due_date"]
            if "due_time" in due and due["due_time"] != (
                current.due_time.strftime("%H:%M") if current.due_time else None
            ):
                body["due_time"] = due["due_time"]
        if (item.description or None) != self._description(current):
            notes = self._notes(current, item.description)
            if notes != current.notes:
                body["notes"] = notes
        if not body:
            return
        answer = await self._call(self.coordinator.client.update_task(item.uid, body))
        self.coordinator.async_apply(Task.from_api(answer))
        # The counts move with it, and a repeating task that moved on left a
        # completed copy behind; both come with the next read.
        await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Into nextasks' trash, where they stay 30 days."""
        trashed: list[str] = []
        try:
            for uid in uids:
                try:
                    await self._call(self.coordinator.client.delete_task(uid))
                except HomeAssistantError as err:
                    if not isinstance(err.__cause__, NextasksNotFoundError):
                        raise
                trashed.append(uid)
        finally:
            self.coordinator.async_forget(trashed)
        await self.coordinator.async_request_refresh()

    async def _call[T](self, request: Awaitable[T]) -> T:
        try:
            result = await request
        except NextasksRefusedError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="refused",
                translation_placeholders={"error": str(err)},
            ) from err
        except NextasksError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        return result
