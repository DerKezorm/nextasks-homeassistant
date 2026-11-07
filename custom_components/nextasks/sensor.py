"""The numbers of nextasks' "Today" page, for dashboards and automations."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DASHBOARD_COUNTS, DASHBOARD_LISTS
from .coordinator import NextasksConfigEntry, NextasksCoordinator
from .entity import account_device

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NextasksConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(NextasksCount(coordinator, key) for key in DASHBOARD_COUNTS)


class NextasksCount(CoordinatorEntity[NextasksCoordinator], SensorEntity):
    """How many open tasks are overdue, due today, and so on.

    Counted by nextasks itself, among the spaces the token sees and without
    hidden ones, subtasks included, the same way its own tiles count.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: NextasksCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}::count::{key}"
        self._attr_device_info = account_device(coordinator)

    @property
    def native_value(self) -> int | None:
        counts = self.coordinator.data.dashboard.get("counts") or {}
        value = counts.get(self._key)
        return int(value) if isinstance(value, int) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._key not in DASHBOARD_LISTS:
            return None
        lists = self.coordinator.data.lists
        tasks = []
        for raw in self.coordinator.data.dashboard.get(self._key) or ():
            task_list = lists.get(str(raw.get("list_id")))
            tasks.append(
                {
                    "title": raw.get("title"),
                    "due_date": raw.get("due_date"),
                    "due_time": raw.get("due_time"),
                    "list": task_list.name if task_list else None,
                    "space": task_list.space_name if task_list else None,
                    "flagged": bool(raw.get("flagged")),
                    "priority": raw.get("priority") or 0,
                    "link": raw.get("link"),
                }
            )
        return {"tasks": tasks}
