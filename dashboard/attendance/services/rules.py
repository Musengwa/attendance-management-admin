"""Session timing rules: is a session running, is someone late. Pure functions of (session, time)."""
from datetime import datetime, timedelta

from django.utils import timezone

from ..models import AppSettings, Session


def make_dt(date, t):
    return timezone.make_aware(datetime.combine(date, t))


def window(session, date):
    return make_dt(date, session.start_time), make_dt(date, session.end_time)


def is_running(session, now):
    """True if `now` falls inside the session (allowing the early window before start)."""
    if not session.is_active:
        return False
    local = timezone.localtime(now)
    if local.weekday() != session.weekday:
        return False
    start, end = window(session, local.date())
    early = timedelta(minutes=AppSettings.load().early_window_minutes)
    return start - early <= now <= end


def running_sessions(now):
    wd = timezone.localtime(now).weekday()
    return [s for s in Session.objects.filter(is_active=True, weekday=wd) if is_running(s, now)]


def classify(member, now):
    """Where does this detection sit relative to the member's own session?"""
    s = member.session
    if not s or not is_running(s, now):
        return {"session": s, "in_session": False, "minutes_late": 0, "is_late": False}
    start, _ = window(s, timezone.localtime(now).date())
    mins = max(0, int((now - start).total_seconds() // 60))
    return {"session": s, "in_session": True, "minutes_late": mins,
            "is_late": mins > AppSettings.load().late_grace_minutes}


def occurrences(session, start_date, end_date, now):
    """Dates in [start_date, end_date] when the session happened AND has already finished."""
    d = start_date
    while d <= end_date:
        if d.weekday() == session.weekday and window(session, d)[1] <= now:
            yield d
        d += timedelta(days=1)
