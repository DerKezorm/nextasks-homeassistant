"""Keeps Home Assistant's copy of the tasks in step with nextasks."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    Me,
    NextasksApiOffError,
    NextasksAuthError,
    NextasksClient,
    NextasksError,
    Task,
    TaskList,
)
from .const import (
    DEFAULT_COMPLETED_DAYS,
    DOMAIN,
    FULL_SYNC_INTERVAL,
    OPT_COMPLETED_DAYS,
    SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

type NextasksConfigEntry = ConfigEntry[NextasksCoordinator]


@dataclass(slots=True)
class NextasksData:
    """Everything the entities read."""

    me: Me
    #: In the order the interface shows them.
    lists: dict[str, TaskList]
    #: Top-level tasks: all open ones, and the recently completed ones.
    tasks: dict[str, Task] = field(default_factory=dict)
    dashboard: dict[str, Any] = field(default_factory=dict)


class NextasksCoordinator(DataUpdateCoordinator[NextasksData]):
    """Polls nextasks, cheaply.

    The first run and one run an hour read everything. The runs in between only
    ask what changed since the last answer (``updated_since`` with the server's
    own ``as_of``), which is one small request when nothing happened.
    """

    config_entry: NextasksConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: NextasksConfigEntry, client: NextasksClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self._as_of: str | None = None
        self._full_at: datetime | None = None

    @property
    def completed_days(self) -> int:
        return int(self.config_entry.options.get(OPT_COMPLETED_DAYS, DEFAULT_COMPLETED_DAYS))

    async def _async_update_data(self) -> NextasksData:
        try:
            return await self._read()
        except NextasksAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="token_invalid"
            ) from err
        except NextasksApiOffError as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="api_off") from err
        except NextasksError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err

    async def _read(self) -> NextasksData:
        now = dt_util.utcnow()
        full = (
            self.data is None
            or self._as_of is None
            or self._full_at is None
            or now - self._full_at >= FULL_SYNC_INTERVAL
        )
        me = await self.client.me() if full or self.data is None else self.data.me
        lists = {item.id: item for item in await self.client.lists()}
        if full:
            tasks = await self._read_everything(now)
            self._full_at = now
        else:
            tasks = await self._read_changes()
        tasks = {key: task for key, task in tasks.items() if task.list_id in lists}
        dashboard = await self.client.dashboard()
        return NextasksData(me=me, lists=lists, tasks=tasks, dashboard=dashboard)

    async def _read_everything(self, now: datetime) -> dict[str, Task]:
        page = await self.client.tasks(state="open")
        tasks = {task.id: task for task in page.tasks}
        self._as_of = page.as_of
        if self.completed_days > 0:
            # A task ticked off in the last days was changed in the last days,
            # so this finds them without walking the whole history.
            since = now - timedelta(days=self.completed_days)
            done = await self.client.tasks(
                state="completed", updated_since=since.strftime("%Y-%m-%dT%H:%M:%SZ")
            )
            for task in done.tasks:
                if not task.trashed:
                    tasks[task.id] = task
        return tasks

    async def _read_changes(self) -> dict[str, Task]:
        assert self.data is not None and self._as_of is not None
        page = await self.client.tasks(updated_since=self._as_of)
        tasks = dict(self.data.tasks)
        for task in page.tasks:
            self._keep(tasks, task)
        for task_id in page.gone:
            tasks.pop(task_id, None)
        if page.as_of:
            self._as_of = page.as_of
        return tasks

    @staticmethod
    def _keep(tasks: dict[str, Task], task: Task) -> None:
        if task.trashed or task.parent_id is not None:
            tasks.pop(task.id, None)
        else:
            tasks[task.id] = task

    @callback
    def async_apply(self, task: Task) -> None:
        """Show a change made from Home Assistant at once, before the next poll."""
        if self.data is None:
            return
        tasks = dict(self.data.tasks)
        self._keep(tasks, task)
        self.async_set_updated_data(replace(self.data, tasks=tasks))

    @callback
    def async_forget(self, task_ids: list[str]) -> None:
        """Take tasks out that were trashed from Home Assistant."""
        if self.data is None:
            return
        tasks = {key: task for key, task in self.data.tasks.items() if key not in task_ids}
        self.async_set_updated_data(replace(self.data, tasks=tasks))
