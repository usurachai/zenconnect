"""Unit tests for working-hours gate (app/services/working_hours.py)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.services.working_hours import is_within_working_hours

BKK = ZoneInfo("Asia/Bangkok")


def make_settings(**kwargs: object) -> Settings:
    return Settings(
        database_url="postgresql://test",
        redis_url="redis://test",
        conversations_webhook_secret="test",
        sunco_key_id="test",
        sunco_key_secret="test",
        sunco_app_id="test",
        integration_key_id="test",
        integration_key_secret="test",
        zendesk_subdomain="test",
        zendesk_api_token="test",
        zendesk_agent_group_id="test",
        rag_base_url="http://test",
        rag_api_key="test",
        **kwargs,
    )


def bkk(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """Helper: a datetime pinned to Asia/Bangkok."""
    return datetime(year, month, day, hour, minute, tzinfo=BKK)


# ---------------------------------------------------------------------------
# Weekday / within-hours  →  True
# ---------------------------------------------------------------------------


def test_monday_midday_is_within_hours() -> None:
    settings = make_settings()
    # 2026-03-16 is a Monday
    assert is_within_working_hours(settings, now=bkk(2026, 3, 16, 12, 0)) is True


def test_friday_end_of_day_is_within_hours() -> None:
    settings = make_settings()
    # 2026-03-20 is a Friday, 17:59 still inside window
    assert is_within_working_hours(settings, now=bkk(2026, 3, 20, 17, 59)) is True


# ---------------------------------------------------------------------------
# Boundary: 09:00 inclusive, 18:00 exclusive
# ---------------------------------------------------------------------------


def test_start_boundary_inclusive() -> None:
    settings = make_settings()
    assert is_within_working_hours(settings, now=bkk(2026, 3, 16, 9, 0)) is True


def test_end_boundary_exclusive() -> None:
    settings = make_settings()
    # Exactly 18:00 should be outside
    assert is_within_working_hours(settings, now=bkk(2026, 3, 16, 18, 0)) is False


def test_before_start_boundary() -> None:
    settings = make_settings()
    assert is_within_working_hours(settings, now=bkk(2026, 3, 16, 8, 59)) is False


# ---------------------------------------------------------------------------
# Weekend  →  False
# ---------------------------------------------------------------------------


def test_saturday_is_outside_hours() -> None:
    settings = make_settings()
    # 2026-03-21 is a Saturday
    assert is_within_working_hours(settings, now=bkk(2026, 3, 21, 10, 0)) is False


def test_sunday_is_outside_hours() -> None:
    settings = make_settings()
    # 2026-03-22 is a Sunday
    assert is_within_working_hours(settings, now=bkk(2026, 3, 22, 10, 0)) is False


# ---------------------------------------------------------------------------
# Timezone awareness: same instant may be in or out depending on local tz
# ---------------------------------------------------------------------------


def test_timezone_conversion_utc_vs_bangkok() -> None:
    """Mon 02:30 UTC == Mon 09:30 Asia/Bangkok → should be within hours."""
    from zoneinfo import ZoneInfo

    settings = make_settings(agent_timezone="Asia/Bangkok")
    utc_time = datetime(2026, 3, 16, 2, 30, tzinfo=ZoneInfo("UTC"))  # 02:30 UTC
    # is_within_working_hours must convert to Bangkok (09:30) → True
    assert is_within_working_hours(settings, now=utc_time) is True


def test_timezone_conversion_outside_hours_in_bangkok() -> None:
    """Mon 01:00 UTC == Mon 08:00 Asia/Bangkok → before 09:00 → False."""
    from zoneinfo import ZoneInfo

    settings = make_settings(agent_timezone="Asia/Bangkok")
    utc_time = datetime(2026, 3, 16, 1, 0, tzinfo=ZoneInfo("UTC"))  # 01:00 UTC
    assert is_within_working_hours(settings, now=utc_time) is False


# ---------------------------------------------------------------------------
# Configurable working days
# ---------------------------------------------------------------------------


def test_custom_working_days_excludes_friday() -> None:
    """If working days = Mon–Thu only, Friday must return False."""
    settings = make_settings(agent_working_days=[0, 1, 2, 3])  # Mon–Thu
    # 2026-03-20 is a Friday
    assert is_within_working_hours(settings, now=bkk(2026, 3, 20, 10, 0)) is False


def test_custom_working_days_includes_saturday() -> None:
    """If Saturday (5) is explicitly added, it should return True during hours."""
    settings = make_settings(agent_working_days=[0, 1, 2, 3, 4, 5])
    # 2026-03-21 is a Saturday
    assert is_within_working_hours(settings, now=bkk(2026, 3, 21, 10, 0)) is True


# ---------------------------------------------------------------------------
# Configurable working hours
# ---------------------------------------------------------------------------


def test_custom_hour_range() -> None:
    settings = make_settings(agent_working_hour_start=8, agent_working_hour_end=20)
    assert is_within_working_hours(settings, now=bkk(2026, 3, 16, 8, 0)) is True
    assert is_within_working_hours(settings, now=bkk(2026, 3, 16, 19, 59)) is True
    assert is_within_working_hours(settings, now=bkk(2026, 3, 16, 20, 0)) is False
    assert is_within_working_hours(settings, now=bkk(2026, 3, 16, 7, 59)) is False
