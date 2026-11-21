"""Session timing rules: is a session running, is someone late. Pure functions of (session, time).

A session meets whenever one of its slots says so: weekly slots on their days, one-off slots on their date.
Each meeting ("occurrence") is a (start, end) pair of aware datetimes, and attendance is counted per occurrence,
so a session that meets at 08:00 and 10:00 on Wednesday has two occurrences that day.
"""
from datetime import datetime, timedelta

from django.utils import timezone

from ..models import AppSettings, Session


def make_dt(date, t):
    return timezone.make_aware(datetime.combine(date, t))


def windows_on(session, date):
    """Every (start, end) this session has on `date`, earliest first."""
    return sorted({(make_dt(date, s.start_time), make_dt(date, s.end_time))
                   for s in session.slots.all() if s.happens_on(date)})


def current_window(session, now):
    """The occurrence `now` belongs to (the early window before start included), or None.
    If two occurrences overlap, the one that started most recently wins."""
    if not session.is_active:
        return None
    early = timedelta(minutes=AppSettings.load().early_window_minutes)
    hits = [w for w in windows_on(session, timezone.localtime(now).date()) if w[0] - early <= now <= w[1]]
    return hits[-1] if hits else None


def is_running(session, now):
    return current_window(session, now) is not None


def running_sessions(now):
    """[(session, (start, end)), ...] for every session running at `now`."""
    out = []
    for s in Session.objects.filter(is_active=True).prefetch_related("slots"):
        w = current_window(s, now)
        if w:
            out.append((s, w))
    return out


def has_occurrence_on(session, date):
    return session.is_active and any(s.happens_on(date) for s in session.slots.all())


def classify(member, now):
    """Where does this detection sit relative to the member's own session?"""
    s = member.session
    w = current_window(s, now) if s else None
    if not w:
        return {"session": s, "in_session": False, "occurrence_start": None, "minutes_late": 0, "is_late": False}
    mins = max(0, int((now - w[0]).total_seconds() // 60))
    return {"session": s, "in_session": True, "occurrence_start": w[0], "minutes_late": mins,
            "is_late": mins > AppSettings.load().late_grace_minutes}


def occurrences(session, start_date, end_date, now):
    """Start datetimes of occurrences in [start_date, end_date] that have already finished, earliest first."""
    out, d = [], start_date
    while d <= end_date:
        out += [start for start, end in windows_on(session, d) if end <= now]
        d += timedelta(days=1)
    return out
