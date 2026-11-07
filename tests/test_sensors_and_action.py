"""The day's numbers, the create_task action and the diagnostics."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nextasks.diagnostics import async_get_config_entry_diagnostics

from .conftest import CHORES, TOKEN, FakeNextasks, setup_entry


async def test_the_counts_are_sensors(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    server.add("Bread", due_date="2026-11-01")
    await setup_entry(hass, entry)
    assert hass.states.get("sensor.nextasks_family_today").state == "2"  # type: ignore[union-attr]
    assert hass.states.get("sensor.nextasks_family_open").state == "4"  # type: ignore[union-attr]
    overdue = hass.states.get("sensor.nextasks_family_overdue")
    assert overdue is not None
    assert overdue.state == "1"
    assert overdue.attributes["tasks"][0]["title"] == "Bread"
    assert overdue.attributes["tasks"][0]["list"] == "Shopping"
    assert overdue.attributes["tasks"][0]["space"] == "Family"


async def test_create_task_carries_what_a_todo_list_cannot(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    await setup_entry(hass, entry)
    answer = await hass.services.async_call(
        "nextasks",
        "create_task",
        {
            "list": "todo.family_chores",
            "title": "Take the bins out",
            "due_date": "2026-11-20",
            "due_time": "07:00",
            "priority": "high",
            "flagged": True,
            "tags": ["house"],
        },
        blocking=True,
        return_response=True,
    )
    body = next(value for call, value in server.calls if call == "create")
    assert body == {
        "list_id": CHORES,
        "title": "Take the bins out",
        "due_date": "2026-11-20",
        "due_time": "07:00",
        "priority": 3,
        "flagged": True,
        "tags": ["house"],
    }
    assert answer is not None
    assert answer["title"] == "Take the bins out"


async def test_create_task_wants_a_nextasks_list(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    await setup_entry(hass, entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "nextasks",
            "create_task",
            {"list": "todo.shopping_somewhere_else", "title": "x"},
            blocking=True,
        )


async def test_a_time_needs_a_day(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    await setup_entry(hass, entry)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "nextasks",
            "create_task",
            {"list": "todo.family_chores", "title": "x", "due_time": "07:00"},
            blocking=True,
        )


async def test_diagnostics_carry_no_token_and_no_tasks(
    hass: HomeAssistant, server: FakeNextasks, entry: MockConfigEntry
) -> None:
    server.add("Something private")
    await setup_entry(hass, entry)
    found = await async_get_config_entry_diagnostics(hass, entry)
    text = repr(found)
    assert TOKEN not in text
    assert "Something private" not in text
    assert found["tasks"] == {"open": 1, "completed": 0}
