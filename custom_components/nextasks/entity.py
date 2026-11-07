"""Devices: one for the connection, one for each space.

Everything is keyed by the config entry, not by the account: the same account
may be added twice with two tokens (its own space, the family's), and a token
renewed through re-authentication keeps its entities.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .api import TaskList
from .const import DOMAIN
from .coordinator import NextasksCoordinator


def account_device(coordinator: NextasksCoordinator) -> DeviceInfo:
    entry = coordinator.config_entry
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="nexapps",
        model="nextasks",
        sw_version=coordinator.data.me.version or None,
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=coordinator.client.url,
    )


def space_device(coordinator: NextasksCoordinator, task_list: TaskList) -> DeviceInfo:
    """A space is a device, so its lists sit together in Home Assistant."""
    entry = coordinator.config_entry
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}::space::{task_list.space_id}")},
        name=task_list.space_name,
        manufacturer="nexapps",
        model="nextasks space",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=coordinator.client.url,
    )


def list_unique_id(coordinator: NextasksCoordinator, list_id: str) -> str:
    return f"{coordinator.config_entry.entry_id}::list::{list_id}"


def list_id_from_unique_id(unique_id: str) -> str | None:
    head, sep, list_id = unique_id.rpartition("::list::")
    return list_id if sep and head else None
