"""Constants for the nextasks integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "nextasks"

CONF_URL: Final = "url"
CONF_TOKEN: Final = "token"

#: Lists the user switched off in the options; everything else shows up.
OPT_EXCLUDED_LISTS: Final = "excluded_lists"
#: How many days a ticked-off task stays visible in Home Assistant. 0 hides them.
OPT_COMPLETED_DAYS: Final = "completed_days"
DEFAULT_COMPLETED_DAYS: Final = 7
MAX_COMPLETED_DAYS: Final = 30

#: How often Home Assistant asks for changes. One small request for the tasks,
#: one for the lists, one for the numbers.
SCAN_INTERVAL: Final = timedelta(seconds=30)
#: Every so often everything is read again, so that nothing the change feed
#: cannot express (an order changed, a subtask ticked off) stays wrong for long.
FULL_SYNC_INTERVAL: Final = timedelta(hours=1)

SERVICE_CREATE_TASK: Final = "create_task"
ATTR_LIST: Final = "list"
ATTR_TITLE: Final = "title"
ATTR_NOTES: Final = "notes"
ATTR_DUE_DATE: Final = "due_date"
ATTR_DUE_TIME: Final = "due_time"
ATTR_PRIORITY: Final = "priority"
ATTR_FLAGGED: Final = "flagged"
ATTR_TAGS: Final = "tags"
ATTR_URL: Final = "url"

PRIORITIES: Final = {"none": 0, "low": 1, "medium": 2, "high": 3}

#: The numbers ``GET /api/v1/dashboard`` counts, each one a sensor.
DASHBOARD_COUNTS: Final = (
    "overdue",
    "today",
    "upcoming",
    "assigned_to_me",
    "flagged",
    "open",
)
#: Counts that come with the tasks behind them.
DASHBOARD_LISTS: Final = ("overdue", "today", "upcoming")
