"""The client against recorded answers: paging, errors, and what it sends."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.nextasks.api import (
    NextasksApiOffError,
    NextasksAuthError,
    NextasksClient,
    NextasksConnectionError,
    NextasksNotFoundError,
    NextasksRefusedError,
)

from .conftest import TOKEN, URL


def task(task_id: str, title: str) -> dict[str, object]:
    return {
        "id": task_id,
        "list_id": "l",
        "title": title,
        "notes": "",
        "completed": False,
        "trashed": False,
        "subtasks": {"open": 0, "total": 0},
    }


async def test_pages_are_followed_and_gone_ids_kept(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        f"{URL}/api/v1/tasks?cursor=2",
        json={
            "tasks": [task("b", "Two"), {"id": "c", "gone": True}],
            "next_cursor": None,
            "as_of": "2026-11-07T10:00:01.000Z",
        },
    )
    aioclient_mock.get(
        f"{URL}/api/v1/tasks",
        json={"tasks": [task("a", "One")], "next_cursor": "2", "as_of": "2026-11-07T10:00:00.000Z"},
    )
    client = NextasksClient(async_get_clientsession(hass), URL + "/", TOKEN)
    page = await client.tasks(state="open")
    assert [item.title for item in page.tasks] == ["One", "Two"]
    assert page.gone == ["c"]
    # The first page's time: what changed while paging comes again next time.
    assert page.as_of == "2026-11-07T10:00:00.000Z"
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert "Origin" not in headers
    assert url.query["state"] == "open"


@pytest.mark.parametrize(
    ("status", "code", "error"),
    [
        (401, "token_invalid", NextasksAuthError),
        (401, "api_off", NextasksApiOffError),
        (403, "token_reads_only", NextasksRefusedError),
        (404, "not_found", NextasksNotFoundError),
        (422, "invalid_input", NextasksRefusedError),
        (429, "slow_down", NextasksConnectionError),
        (502, None, NextasksConnectionError),
    ],
)
async def test_errors_are_told_apart(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    code: str | None,
    error: type[Exception],
) -> None:
    body = {"detail": {"code": code, "message": "Said no."}} if code else None
    aioclient_mock.get(f"{URL}/api/v1/me", status=status, json=body)
    client = NextasksClient(async_get_clientsession(hass), URL, TOKEN)
    with pytest.raises(error):
        await client.me()


async def test_a_server_that_is_not_there(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"{URL}/api/v1/me", exc=TimeoutError())
    client = NextasksClient(async_get_clientsession(hass), URL, TOKEN)
    with pytest.raises(NextasksConnectionError):
        await client.me()


async def test_writes_send_json_and_trash_answers_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{URL}/api/v1/tasks", json=task("n", "New"), status=201)
    aioclient_mock.patch(f"{URL}/api/v1/tasks/n", json={**task("n", "New"), "completed": True})
    aioclient_mock.delete(f"{URL}/api/v1/tasks/n", status=204)
    client = NextasksClient(async_get_clientsession(hass), URL, TOKEN)
    made = await client.create_task({"title": "New"})
    assert made.id == "n"
    changed = await client.update_task("n", {"completed": True})
    assert changed["completed"] is True
    assert await client.delete_task("n") is None
    assert aioclient_mock.mock_calls[0][2] == {"title": "New"}
