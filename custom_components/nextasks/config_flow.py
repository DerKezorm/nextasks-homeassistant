"""Setting nextasks up: an address and a token, checked before anything is saved."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    Me,
    NextasksApiOffError,
    NextasksAuthError,
    NextasksClient,
    NextasksConnectionError,
    NextasksError,
)
from .const import (
    CONF_TOKEN,
    CONF_URL,
    DEFAULT_COMPLETED_DAYS,
    DOMAIN,
    MAX_COMPLETED_DAYS,
    OPT_COMPLETED_DAYS,
    OPT_EXCLUDED_LISTS,
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        vol.Required(CONF_TOKEN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    }
)

TOKEN_SCHEMA = vol.Schema(
    {vol.Required(CONF_TOKEN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


def account_id(url: str, me: Me) -> str:
    """Server and account: a renewed token must belong to the same one."""
    return f"{url}::{me.name}"


def connection_id(url: str, me: Me) -> str:
    """Server, account and the spaces the token was given.

    ⚠️ Not just the account: one account may well be added twice, with one token
    for its own space and one for the family's, so that each can be shown to
    different people. The same spaces twice would only show everything twice.
    """
    spaces = ",".join(sorted(space_id for space_id, _ in me.spaces))
    return f"{account_id(url, me)}::{spaces}"


def connection_title(me: Me) -> str:
    names = [name for _, name in me.spaces if name]
    return f"nextasks {', '.join(names)}" if names else f"nextasks {me.display_name}"


def clean_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.endswith("/api/v1"):
        url = url[: -len("/api/v1")]
    return url


class NextasksConfigFlow(ConfigFlow, domain=DOMAIN):
    """Address and token."""

    VERSION = 1

    async def _check(self, url: str, token: str) -> tuple[Me | None, dict[str, str]]:
        client = NextasksClient(async_get_clientsession(self.hass), url, token)
        try:
            me = await client.me()
            await client.lists()
        except NextasksAuthError:
            return None, {"base": "invalid_auth"}
        except NextasksApiOffError:
            return None, {"base": "api_off"}
        except NextasksConnectionError:
            return None, {"base": "cannot_connect"}
        except NextasksError:
            return None, {"base": "unknown"}
        return me, {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = clean_url(user_input[CONF_URL])
            token = user_input[CONF_TOKEN].strip()
            me, errors = await self._check(url, token)
            if me is not None:
                await self.async_set_unique_id(connection_id(url, me))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=connection_title(me),
                    data={CONF_URL: url, CONF_TOKEN: token},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A token runs out after 90 days unless picked otherwise; a new one goes here."""
        entry = self._get_reauth_entry()
        url = entry.data[CONF_URL]
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            me, errors = await self._check(url, token)
            if me is not None:
                if not (entry.unique_id or "").startswith(account_id(url, me) + "::"):
                    return self.async_abort(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=connection_id(url, me),
                    data_updates={CONF_TOKEN: token},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=TOKEN_SCHEMA,
            description_placeholders={"url": url, "title": entry.title},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NextasksOptionsFlow:
        return NextasksOptionsFlow()


class NextasksOptionsFlow(OptionsFlow):
    """Which lists to show, and for how long ticked-off tasks stay."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        coordinator = getattr(entry, "runtime_data", None)
        # Lists of spaces the account hid are never shown, so they are not offered either.
        known = coordinator.data.lists.values() if coordinator and coordinator.data else ()
        lists = [item for item in known if not item.hidden]
        if user_input is not None:
            shown = set(user_input.get("shown_lists") or ())
            return self.async_create_entry(
                data={
                    OPT_EXCLUDED_LISTS: [item.id for item in lists if item.id not in shown],
                    OPT_COMPLETED_DAYS: int(user_input[OPT_COMPLETED_DAYS]),
                }
            )
        excluded = set(entry.options.get(OPT_EXCLUDED_LISTS) or ())
        choices = [
            SelectOptionDict(value=item.id, label=f"{item.space_name}: {item.name}")
            for item in lists
        ]
        schema = vol.Schema(
            {
                vol.Optional(
                    "shown_lists",
                    default=[item["value"] for item in choices if item["value"] not in excluded],
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=choices, multiple=True, mode=SelectSelectorMode.LIST
                    )
                ),
                vol.Required(
                    OPT_COMPLETED_DAYS,
                    default=entry.options.get(OPT_COMPLETED_DAYS, DEFAULT_COMPLETED_DAYS),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=MAX_COMPLETED_DAYS, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
