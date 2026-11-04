from datetime import timedelta

from django.utils import timezone

from ..models import AppSettings, AttendanceRecord, Member, Notification
from . import rules


def notify(kind, message, member=None, dedupe_minutes=0, now=None):
    """Create a notification unless an equivalent one was already raised recently."""
    now = now or timezone.now()
    if dedupe_minutes:
        q = Notification.objects.filter(kind=kind, created_at__gte=now - timedelta(minutes=dedupe_minutes))
        if member:
            q = q.filter(member=member)
        if q.exists():
            return None
    return Notification.objects.create(kind=kind, message=message, member=member, created_at=now)


def check_imposter_spike(now):
    cfg = AppSettings.load()
    since = now - timedelta(minutes=cfg.imposter_spike_minutes)
    n = AttendanceRecord.objects.filter(status=AttendanceRecord.UNKNOWN, timestamp__gte=since).count()
    if n >= cfg.imposter_spike_count:
        notify(Notification.IMPOSTER_SPIKE,
               f"{n} unregistered faces detected in the last {cfg.imposter_spike_minutes} minutes.",
               dedupe_minutes=cfg.imposter_spike_minutes, now=now)


def check_inactivity(now=None):
    """Alert for active members who missed their last N scheduled sessions in a row."""
    now = now or timezone.now()
    n = AppSettings.load().inactive_sessions_threshold
    today = timezone.localdate(now)
    members = Member.objects.filter(status=Member.ACTIVE, session__isnull=False,
                                    session__is_active=True).select_related("session")
    for m in members:
        first = timezone.localdate(m.registered_at)
        dates = sorted(rules.occurrences(m.session, max(first, today - timedelta(days=365)), today, now))
        last_n = dates[-n:]
        if len(last_n) < n:
            continue  # not enough sessions since registration to judge
        present = {timezone.localdate(r.timestamp) for r in AttendanceRecord.objects.filter(
            member=m, status=AttendanceRecord.PRESENT, in_session=True)}
        if not present.intersection(last_n):
            notify(Notification.INACTIVE, f"{m.full_name} has missed the last {n} sessions of {m.session.name}.",
                   member=m, dedupe_minutes=60 * 24 * 7, now=now)
