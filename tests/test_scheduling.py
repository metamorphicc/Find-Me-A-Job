from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from job_search_automation.config import ConfigError, ScheduleConfig, load_config
from job_search_automation.scheduling import DailyScheduler


def test_daily_schedule_runs_once_per_local_day(tmp_path):
    schedule = DailyScheduler(
        ScheduleConfig(True, "09:00", "Asia/Novosibirsk"), tmp_path / "schedule-state.json"
    )
    zone = ZoneInfo("Asia/Novosibirsk")
    before = datetime(2026, 9, 24, 8, 59, tzinfo=zone)
    due = datetime(2026, 9, 24, 9, 0, tzinfo=zone)
    next_day = datetime(2026, 9, 25, 9, 0, tzinfo=zone)
    assert schedule.due(before) is None
    assert schedule.due(due) == "2026-09-24"
    schedule.mark("2026-09-24")
    assert schedule.due(due) is None
    assert schedule.due(next_day) == "2026-09-25"


def test_invalid_schedule_time_is_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[search]\nqueries = ["Python"]\n[schedule]\nenabled = true\ntime = "25:00"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="schedule.time"):
        load_config(path)
