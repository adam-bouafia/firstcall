"""When to page engineers.

Two modes:
  always    page 24/7 (default)
  offhours  page only outside business hours: weekdays 17:00-09:00 and all weekend
            (business hours are when people are at their desks watching the console anyway)

The mode can be changed at runtime (UI, API, Telegram /mode) and is persisted in SQLite.
The console, Kubernetes Events and metrics are never suppressed: only pushes to phones are.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

MODES = ("always", "offhours")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _hhmm(s: str) -> time:
    h, m = s.strip().split(":")
    return time(int(h), int(m))


def _days(spec: str) -> set[int]:
    """'mon-fri' or 'mon,tue,wed' -> {0..4}"""
    out: set[int] = set()
    for part in spec.lower().replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-")
            i, j = DAYS.index(a), DAYS.index(b)
            out |= set(range(i, j + 1))
        elif part:
            out.add(DAYS.index(part))
    return out


@dataclass
class Schedule:
    timezone: str = "Europe/Amsterdam"
    business_start: str = "09:00"
    business_end: str = "17:00"
    business_days: str = "mon-fri"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def is_business_hours(self, when: datetime | None = None) -> bool:
        when = (when or self.now()).astimezone(self.tz)
        if when.weekday() not in _days(self.business_days):
            return False
        return _hhmm(self.business_start) <= when.time() < _hhmm(self.business_end)

    def should_page(self, mode: str, when: datetime | None = None) -> bool:
        return mode == "always" or not self.is_business_hours(when)

    def next_change(self, mode: str, when: datetime | None = None) -> datetime | None:
        """When paging turns on/off next (None in 'always' mode). Minute resolution, max 8 days."""
        if mode == "always":
            return None
        when = (when or self.now()).astimezone(self.tz).replace(second=0, microsecond=0)
        current = self.should_page(mode, when)
        t = when
        for _ in range(8 * 24 * 4):  # 15-minute steps are enough for HH:00 / HH:30 boundaries
            t += timedelta(minutes=15)
            if self.should_page(mode, t) != current:
                # refine to the minute
                s = t - timedelta(minutes=15)
                while self.should_page(mode, s) == current:
                    s += timedelta(minutes=1)
                return s
        return None

    def describe(self, mode: str) -> str:
        if mode == "always":
            return "paging 24/7"
        return (f"paging outside {self.business_start}-{self.business_end} "
                f"{self.business_days} ({self.timezone}) and all weekend")
