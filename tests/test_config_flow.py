"""Setting up, a second token for the same account, a renewed token, the options."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nextasks.api import (
    NextasksApiOffError,
    NextasksAuthError,
    NextasksConnectionError,
    NextasksError,
)
from custom_components.nextasks.const import (
    CONF_TOKEN,
    CONF_URL,
    DOMAIN,
    OPT_COMPLETED_DAYS,
    OPT_EXCLUDED_LISTS,
)

from .conftest import CHORES, FAMILY, SHOPPING, TOKEN, URL, FakeNextasks, setup_entry

PRIVATE = "33333333-3333-4333-8333-333333333333"


async def start(hass: HomeAssistant, url: str = URL + "/", token: str = TOKEN) -> dict:  # type: ignore[type-arg]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(  # type: ignore[return-value]
        result["flow_id"], {CONF_URL: url, CONF_TOKEN: f" {token} "}
    )


async def test_a_working_token_makes_an_entry(hass: HomeAssistant, server: FakeNextasks) -> None:
    result = await start(hass, url=URL + "/api/v1/")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "nextasks Family"
    assert result["data"] == {CONF_URL: URL, CONF_TOKEN: TOKEN}
    assert result["result"].unique_id == f"{URL}::robin::{FAMILY}"


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        (NextasksAuthError("x"), "invalid_auth"),
        (NextasksApiOffError("x"), "api_off"),
        (NextasksConnectionError("x"), "cannot_connect"),
        (NextasksError("x"), "unknown"),
    ],
)
async def test_what_goes_wrong_is_named(
    hass: HomeAssistant, server: FakeNextasks, error: Exception, shown: str
) -> None:
    server.fail = error
    result = await start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": shown}

    server.fail = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL, CONF_TOKEN: TOKEN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_the_same_spaces_twice_are_refused(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    entry.add_to_hass(hass)
    result = await start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_a_second_token_for_other_spaces_is_its_own_entry(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    """One token for the family on the wall, one for the account's own tasks."""
    entry.add_to_hass(hass)
    server.me["spaces"] = [{"id": PRIVATE, "name": "Private"}]
    result = await start(hass)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "nextasks Private"


async def test_a_renewed_token_replaces_the_old_one(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    await setup_entry(hass, entry)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "ntk_" + "n" * 40}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == "ntk_" + "n" * 40


async def test_a_token_of_another_account_is_refused(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    await setup_entry(hass, entry)
    server.me["name"] = "sam"
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TOKEN: "ntk_" + "o" * 40}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data[CONF_TOKEN] == TOKEN


async def test_options_switch_lists_off(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    await setup_entry(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"shown_lists": [SHOPPING], OPT_COMPLETED_DAYS: 0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options == {OPT_EXCLUDED_LISTS: [CHORES], OPT_COMPLETED_DAYS: 0}
    assert hass.states.get("todo.family_shopping") is not None
    assert er.async_get(hass).async_get("todo.family_chores") is None
