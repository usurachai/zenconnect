from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import Settings


def is_within_working_hours(settings: Settings, now: datetime | None = None) -> bool:
    """Return True if *now* falls within the configured working window.

    Args:
        settings: App settings that carry timezone, working days, and hour range.
        now: The moment to test.  Defaults to the current wall-clock time.
             Pass an explicit value in tests to avoid depending on real time.
    """
    tz = ZoneInfo(settings.agent_timezone)
    local_now = datetime.now(tz) if now is None else now.astimezone(tz)

    day_ok = local_now.weekday() in settings.agent_working_days
    hour_ok = settings.agent_working_hour_start <= local_now.hour < settings.agent_working_hour_end
    return day_ok and hour_ok
