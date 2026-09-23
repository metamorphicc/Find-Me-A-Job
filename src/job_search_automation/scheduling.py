from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from job_search_automation.config import ScheduleConfig


class DailyScheduler:
    def __init__(self, schedule: ScheduleConfig, state_path: Path) -> None:
        self.schedule = schedule
        self.state_path = state_path

    def due(self, now: datetime | None = None) -> str | None:
        if not self.schedule.enabled:
            return None
        local = (now or datetime.now(ZoneInfo(self.schedule.timezone))).astimezone(
            ZoneInfo(self.schedule.timezone)
        )
        day = local.date().isoformat()
        hour, minute = map(int, self.schedule.time.split(":"))
        if (local.hour, local.minute) < (hour, minute):
            return None
        try:
            last = json.loads(self.state_path.read_text(encoding="utf-8")).get("last_run")
        except (OSError, ValueError, AttributeError):
            last = None
        return day if last != day else None

    def mark(self, day: str) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"last_run": day}), encoding="utf-8")
        os.replace(temporary, self.state_path)
