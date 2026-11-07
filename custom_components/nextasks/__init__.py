"""nextasks in Home Assistant: every list a to-do list, plus the day's numbers."""

from __future__ import annotations

from datetime import date, time
from typing import Any

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import (
    NextasksApiOffError,
    NextasksAuthError,
    NextasksClient,
    NextasksError,
    NextasksRefusedError,
)
from .const import (
    ATTR_DUE_DATE,
    ATTR_DUE_TIME,
    ATTR_FLAGGED,
    ATTR_LIST,
    ATTR_NOTES,
    ATTR_PRIORITY,
    ATTR_TAGS,
    ATTR_TITLE,
    ATTR_URL,
    CONF_TOKEN,
    CONF_URL,
    DOMAIN,
    PRIORITIES,
    SERVICE_CREATE_TASK,
)
from .coordinator import NextasksConfigEntry, NextasksCoordinator
from .entity import list_id_from_unique_id

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.TODO]

CREATE_TASK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_LIST): cv.entity_id,
        vol.Required(ATTR_TITLE): vol.All(cv.string, vol.Length(min=1, max=500)),
        vol.Optional(ATTR_NOTES): cv.string,
        vol.Optional(ATTR_DUE_DATE): cv.date,
        vol.Optional(ATTR_DUE_TIME): cv.time,
        vol.Optional(ATTR_PRIORITY): vol.In(list(PRIORITIES)),
        vol.Optional(ATTR_FLAGGED): cv.boolean,
        vol.Optional(ATTR_TAGS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_URL): cv.url,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the action once, for every nextasks entry."""

    async def create_task(call: ServiceCall) -> ServiceResponse:
        return await _create_task(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_CREATE_TASK,
        create_task,
        schema=CREATE_TASK_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NextasksConfigEntry) -> bool:
    client = NextasksClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN]
    )
    coordinator = NextasksCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: NextasksConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NextasksConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _coordinator_for(hass: HomeAssistant, entity_id: str) -> tuple[NextasksCoordinator, str]:
    """The entry and the nextasks list behind a to-do entity."""
    item = er.async_get(hass).async_get(entity_id)
    list_id = list_id_from_unique_id(item.unique_id) if item else None
    entry = (
        hass.config_entries.async_get_entry(item.config_entry_id)
        if item and item.config_entry_id
        else None
    )
    if (
        item is None
        or item.platform != DOMAIN
        or list_id is None
        or entry is None
        or entry.domain != DOMAIN
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="not_a_list",
            translation_placeholders={"entity_id": entity_id},
        )
    coordinator = getattr(entry, "runtime_data", None)
    if not isinstance(coordinator, NextasksCoordinator):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    return coordinator, list_id


async def _create_task(hass: HomeAssistant, call: ServiceCall) -> ServiceResponse:
    coordinator, list_id = _coordinator_for(hass, call.data[ATTR_LIST])
    body: dict[str, Any] = {"list_id": list_id, "title": call.data[ATTR_TITLE]}
    if notes := call.data.get(ATTR_NOTES):
        body["notes"] = notes
    due_date: date | None = call.data.get(ATTR_DUE_DATE)
    due_time: time | None = call.data.get(ATTR_DUE_TIME)
    if due_time is not None and due_date is None:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="time_without_date")
    if due_date is not None:
        body["due_date"] = due_date.isoformat()
    if due_time is not None:
        body["due_time"] = due_time.strftime("%H:%M")
    if (priority := call.data.get(ATTR_PRIORITY)) is not None:
        body["priority"] = PRIORITIES[priority]
    if (flagged := call.data.get(ATTR_FLAGGED)) is not None:
        body["flagged"] = flagged
    if tags := call.data.get(ATTR_TAGS):
        body["tags"] = tags
    if url := call.data.get(ATTR_URL):
        body["url"] = url
    try:
        task = await coordinator.client.create_task(body)
    except (NextasksAuthError, NextasksApiOffError) as err:
        coordinator.config_entry.async_start_reauth(hass)
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="token_invalid"
        ) from err
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
    coordinator.async_apply(task)
    await coordinator.async_request_refresh()
    return {"id": task.id, "title": task.title, "link": task.link}
