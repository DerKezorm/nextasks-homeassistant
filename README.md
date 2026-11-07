# nextasks for Home Assistant

Brings [nextasks](https://github.com/DerKezorm/nextasks), the self-hosted task app for families and small groups, into
Home Assistant: every list becomes a to-do list, and the day's numbers become sensors.

> **Early days.** This is a first version. It works with the nextasks API as of nextasks 0.1.0. Expect rough edges.

## What you get

- **One to-do list per nextasks list.** They show up under *To-do lists* in the sidebar, on dashboards with the to-do
  list card, in automations, and in Assist ("add milk to the shopping list"). Lists are grouped by space, and a new
  list in nextasks appears on its own.
- **Add, edit, tick off, remove.** Title, due date (with or without a time), and notes. Removing a task moves it to the
  nextasks trash, where it stays for 30 days.
- **Sensors** for what nextasks' own *Today* page counts: overdue, today, upcoming, assigned to me, bookmarked, open.
  Overdue, today and upcoming carry the tasks behind the number as an attribute, handy for wall tablets.
- **An action, `nextasks.create_task`,** for what a to-do list cannot carry: priority, bookmark, tags, a link, a due
  time. It answers with the new task's id and link.
- **Several connections.** Add one token for the family's space and another for your own, and show each to whom it
  concerns.

## What Home Assistant cannot show

- **Sections** inside a list: tasks show in one run, in the order nextasks has them.
- **Subtasks:** only top-level tasks are listed. A task with subtasks says how many are done at the end of its
  description.
- **Assignees, priority, bookmarks, tags** are kept as they are, but not shown.
- **Reordering** by drag and drop is not offered yet.

Changes made in nextasks reach Home Assistant within 30 seconds. Changes made in Home Assistant show at once.

## Install

With [HACS](https://hacs.xyz): *HACS → ⋮ → Custom repositories*, add `https://github.com/DerKezorm/nextasks-homeassistant`
as *Integration*, install **nextasks**, and restart Home Assistant.

By hand: copy `custom_components/nextasks` into your Home Assistant's `custom_components` folder and restart.

## Set up

1. In nextasks, the operator switches API tokens on under **Settings → Server → API**.
2. Under **My account → Connections**, make a token. Pick the spaces Home Assistant may see, and **Read and write** if
   the lists should be editable from Home Assistant. A token runs out after 90 days unless you pick otherwise; Home
   Assistant then asks for a new one.
3. In Home Assistant: **Settings → Devices & services → Add integration → nextasks**, then enter the address you open
   nextasks with and the token.

Everyone who uses Home Assistant sees the lists of a connection: Home Assistant has no per-person rights for to-do
lists. To keep your own tasks off the shared wall tablet, give them a token of their own and show their cards only to
yourself (card visibility by user), and do not expose them to Assist.

**Options** (*Configure* on the integration): which lists to show, and for how many days completed tasks stay visible
(7 by default, 0 for none). Lists of spaces you hid in nextasks are never shown.

## Example

```yaml
action: nextasks.create_task
data:
  list: todo.family_chores
  title: Take the bins out
  due_date: "{{ (now() + timedelta(days=1)).date() }}"
  due_time: "07:00"
  priority: high
```

## Development

The tests run on Linux (Home Assistant's test helpers do not support Windows):

```bash
python3.14 -m venv .venv && .venv/bin/pip install -r requirements-test.txt
.venv/bin/ruff check custom_components tests
.venv/bin/mypy custom_components/nextasks --strict --ignore-missing-imports
.venv/bin/pytest
```

## License

MIT
