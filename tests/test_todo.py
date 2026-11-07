"""Lists, items and changes, as Home Assistant's to-do actions see them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.todo import TodoListEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.nextasks.api import NextasksAuthError, NextasksRefusedError
from custom_components.nextasks.const import SCAN_INTERVAL

from .conftest import CHORES, SHOPPING, FakeNextasks, days_ago, setup_entry

SHOPPING_ENTITY = "todo.family_shopping"
CHORES_ENTITY = "todo.family_chores"


async def items(hass: HomeAssistant, entity_id: str = SHOPPING_ENTITY) -> list[dict[str, Any]]:
    answer = await hass.services.async_call(
        "todo",
        "get_items",
        {},
        target={"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )
    result: list[dict[str, Any]] = answer[entity_id]["items"]  # type: ignore[index]
    return result


async def poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(SCAN_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_every_shown_list_is_a_todo_list(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    await setup_entry(hass, entry)
    assert hass.states.get(SHOPPING_ENTITY) is not None
    assert hass.states.get(CHORES_ENTITY) is not None
    # The space the account hid in nextasks stays hidden here too.
    assert hass.states.get("todo.attic_secret") is None
    assert hass.states.get(SHOPPING_ENTITY).state == "0"  # type: ignore[union-attr]


async def test_items_carry_title_due_notes_and_subtasks(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    server.add("Bread", due_date="2026-11-20")
    server.add(
        "Call the plumber",
        due_date="2026-11-20",
        due_time="09:30",
        notes="**Before** noon",
        subtasks={"open": 1, "total": 3},
    )
    server.add("Milk", completed=True, completed_at=days_ago(1))
    server.add(
        "Old one",
        completed=True,
        completed_at=days_ago(20),
        _changed=datetime.now(UTC) - timedelta(days=20),
    )
    server.add("Somewhere else", list_id=CHORES)
    await setup_entry(hass, entry)

    found = await items(hass)
    by_title = {item["summary"]: item for item in found}
    assert list(by_title) == ["Bread", "Call the plumber", "Milk"]
    assert by_title["Bread"]["due"] == "2026-11-20"
    assert "description" not in by_title["Bread"]
    # A time of day is the account's (Berlin), whatever Home Assistant's own zone is.
    plumber = by_title["Call the plumber"]
    due = datetime.fromisoformat(plumber["due"])
    assert due.astimezone(UTC) == datetime(2026, 11, 20, 8, 30, tzinfo=UTC)
    assert plumber["description"] == "**Before** noon\n\nSubtasks: 2 of 3 done"
    assert by_title["Milk"]["status"] == "completed"
    assert hass.states.get(SHOPPING_ENTITY).state == "2"  # type: ignore[union-attr]


async def test_adding_an_item_creates_a_task(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    await setup_entry(hass, entry)
    await hass.services.async_call(
        "todo",
        "add_item",
        {"item": "Eggs", "due_datetime": "2026-11-20 18:00:00", "description": "A dozen"},
        target={"entity_id": SHOPPING_ENTITY},
        blocking=True,
    )
    body = next(value for call, value in server.calls if call == "create")
    # Home Assistant's zone in the tests is US/Pacific: 18:00 there is 03:00 in Berlin.
    assert body == {
        "list_id": SHOPPING,
        "title": "Eggs",
        "notes": "A dozen",
        "due_date": "2026-11-21",
        "due_time": "03:00",
    }
    # Shown at once, without waiting for the next poll.
    assert [item["summary"] for item in await items(hass)] == ["Eggs"]


async def test_adding_with_a_day_only_sends_no_time(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    await setup_entry(hass, entry)
    await hass.services.async_call(
        "todo",
        "add_item",
        {"item": "Stamps", "due_date": "2026-11-22"},
        target={"entity_id": SHOPPING_ENTITY},
        blocking=True,
    )
    body = next(value for call, value in server.calls if call == "create")
    assert body == {"list_id": SHOPPING, "title": "Stamps", "due_date": "2026-11-22"}


async def test_ticking_off_and_renaming_send_only_what_changed(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    task = server.add("Bread", due_date="2026-11-20", notes="Rye", subtasks={"open": 1, "total": 1})
    await setup_entry(hass, entry)

    await hass.services.async_call(
        "todo",
        "update_item",
        {"item": task["id"], "status": "completed"},
        target={"entity_id": SHOPPING_ENTITY},
        blocking=True,
    )
    assert ("update", (task["id"], {"completed": True})) in server.calls

    await hass.services.async_call(
        "todo",
        "update_item",
        {"item": task["id"], "rename": "Rye bread"},
        target={"entity_id": SHOPPING_ENTITY},
        blocking=True,
    )
    assert ("update", (task["id"], {"title": "Rye bread"})) in server.calls


async def test_editing_the_description_keeps_the_subtask_line_out_of_the_notes(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    task = server.add("Bread", notes="Rye", subtasks={"open": 0, "total": 2})
    await setup_entry(hass, entry)
    await hass.services.async_call(
        "todo",
        "update_item",
        {"item": task["id"], "description": "Rye, sliced\n\nSubtasks: 2 of 2 done"},
        target={"entity_id": SHOPPING_ENTITY},
        blocking=True,
    )
    assert ("update", (task["id"], {"notes": "Rye, sliced"})) in server.calls


async def test_clearing_the_due_date(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    task = server.add("Bread", due_date="2026-11-20", due_time="10:00")
    await setup_entry(hass, entry)
    await hass.services.async_call(
        "todo",
        "update_item",
        {"item": task["id"], "due_datetime": None},
        target={"entity_id": SHOPPING_ENTITY},
        blocking=True,
    )
    assert ("update", (task["id"], {"due_date": None})) in server.calls


async def test_removing_items_moves_them_to_the_trash(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    keep = server.add("Bread")
    drop = server.add("Milk")
    await setup_entry(hass, entry)
    await hass.services.async_call(
        "todo",
        "remove_item",
        {"item": [drop["id"]]},
        target={"entity_id": SHOPPING_ENTITY},
        blocking=True,
    )
    assert ("delete", drop["id"]) in server.calls
    assert [item["uid"] for item in await items(hass)] == [keep["id"]]


async def test_a_refusal_is_shown_and_changes_nothing(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    task = server.add("Bread")
    await setup_entry(hass, entry)
    server.fail = NextasksRefusedError("The account may not write here.", "not_allowed", 403)
    with pytest.raises(HomeAssistantError, match="may not write"):
        await hass.services.async_call(
            "todo",
            "update_item",
            {"item": task["id"], "status": "completed"},
            target={"entity_id": SHOPPING_ENTITY},
            blocking=True,
        )


async def test_a_read_token_offers_no_changes(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    server.me["level"] = "read"
    server.lists[1]["writable"] = False
    await setup_entry(hass, entry)
    for entity_id in (SHOPPING_ENTITY, CHORES_ENTITY):
        features = hass.states.get(entity_id).attributes["supported_features"]  # type: ignore[union-attr]
        assert features == 0


async def test_a_list_without_write_right_offers_no_changes(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    server.lists[1]["writable"] = False
    await setup_entry(hass, entry)
    shopping = hass.states.get(SHOPPING_ENTITY).attributes["supported_features"]  # type: ignore[union-attr]
    chores = hass.states.get(CHORES_ENTITY).attributes["supported_features"]  # type: ignore[union-attr]
    assert shopping & TodoListEntityFeature.CREATE_TODO_ITEM
    assert chores == 0


async def test_between_full_reads_only_changes_are_asked_for(
    hass: HomeAssistant,
    server: FakeNextasks,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    bread = server.add("Bread")
    milk = server.add("Milk")
    await setup_entry(hass, entry)
    server.calls.clear()

    freezer.tick(timedelta(seconds=5))
    server.change(bread["id"], title="Rye bread")
    server.change(milk["id"], trashed=True)
    server.add("Butter")
    await poll(hass, freezer)

    task_calls = [value for call, value in server.calls if call == "tasks"]
    assert len(task_calls) == 1
    assert set(task_calls[0]) == {"updated_since"}
    assert [item["summary"] for item in await items(hass)] == ["Rye bread", "Butter"]


async def test_a_task_gone_for_good_disappears(
    hass: HomeAssistant,
    server: FakeNextasks,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    bread = server.add("Bread")
    await setup_entry(hass, entry)
    freezer.tick(timedelta(seconds=5))
    del server.tasks[bread["id"]]
    server.gone[bread["id"]] = datetime.now(UTC)
    await poll(hass, freezer)
    assert await items(hass) == []


async def test_new_lists_appear_and_deleted_ones_go(
    hass: HomeAssistant,
    server: FakeNextasks,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_entry(hass, entry)
    server.lists[0]["name"] = "Groceries"
    server.lists.pop(1)
    server.lists.append(
        server._list(
            "dddddddd-dddd-4ddd-8ddd-dddddddddddd", server.lists[0]["space_id"], "Family", "Garden"
        )
    )
    await poll(hass, freezer)

    registry = er.async_get(hass)
    assert hass.states.get("todo.family_garden") is not None
    assert registry.async_get(CHORES_ENTITY) is None
    # A renamed list keeps its entity id and shows its new name.
    state = hass.states.get(SHOPPING_ENTITY)
    assert state is not None
    assert state.attributes["friendly_name"] == "Family Groceries"


async def test_an_expired_token_asks_for_a_new_one(
    hass: HomeAssistant,
    server: FakeNextasks,
    entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await setup_entry(hass, entry)
    server.fail = NextasksAuthError("No valid token.", "token_invalid", 401)
    await poll(hass, freezer)
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]
