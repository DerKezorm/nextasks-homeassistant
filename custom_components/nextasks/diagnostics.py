"""What a bug report may carry: shapes and counts, never the token or the tasks."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_TOKEN
from .coordinator import NextasksConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NextasksConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), {CONF_TOKEN}),
        "options": dict(entry.options),
        "server": {
            "version": data.me.version,
            "level": data.me.level,
            "features": list(data.me.features),
            "time_zone": data.me.time_zone,
        },
        "spaces": len(data.me.spaces),
        "lists": [
            {"writable": item.writable, "hidden": item.hidden} for item in data.lists.values()
        ],
        "tasks": {
            "open": sum(1 for task in data.tasks.values() if not task.completed),
            "completed": sum(1 for task in data.tasks.values() if task.completed),
        },
        "counts": data.dashboard.get("counts"),
        "last_update_success": coordinator.last_update_success,
    }
