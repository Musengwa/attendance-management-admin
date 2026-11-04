"""Attendance rate, tardiness rate and imposter rate for the Settings page."""
from datetime import time, timedelta

from django.utils import timezone

from ..models import AttendanceRecord as R
from ..models import Member
from . import rules


def pct(a, b):
    return round(100 * a / b, 1) if b else None


def compute(days=30, now=None):
    now = now or timezone.now()
    today = timezone.localdate(now)
    start = today - timedelta(days=days - 1)
    start_dt = rules.make_dt(start, time.min)

    # first on-time/late present record for each (member, date)
    pairs = {}
    for r in R.objects.filter(status=R.PRESENT, in_session=True, timestamp__gte=start_dt).order_by("timestamp"):
        pairs.setdefault((r.member_id, timezone.localdate(r.timestamp)), r.is_late)

    per, exp, pres, late = {}, 0, 0, 0
    members = Member.objects.filter(status=Member.ACTIVE, session__isnull=False,
                                    session__is_active=True).select_related("session")
    for m in members:
        first = max(start, timezone.localdate(m.registered_at))
        for d in rules.occurrences(m.session, first, today, now):
            row = per.setdefault(m.session_id, {"name": m.session.name, "expected": 0, "present": 0, "late": 0})
            row["expected"] += 1
            exp += 1
            if (m.pk, d) in pairs:
                row["present"] += 1
                pres += 1
                if pairs[(m.pk, d)]:
                    row["late"] += 1
                    late += 1
    for row in per.values():
        row["attendance_rate"] = pct(row["present"], row["expected"])
        row["tardiness_rate"] = pct(row["late"], row["present"])

    dets = R.objects.filter(timestamp__gte=start_dt)
    total, imposters = dets.count(), dets.filter(status=R.UNKNOWN).count()
    daily = []
    for i in range(13, -1, -1):
        d = today - timedelta(days=i)
        daily.append({"label": d.strftime("%d %b"), "count": dets.filter(status=R.UNKNOWN, timestamp__date=d).count()})
    peak = max([x["count"] for x in daily] + [1])
    for x in daily:
        x["height"] = int(100 * x["count"] / peak)

    return {
        "days": days, "expected": exp, "present": pres, "late": late,
        "attendance_rate": pct(pres, exp), "tardiness_rate": pct(late, pres),
        "detections": total, "imposters": imposters, "imposter_rate": pct(imposters, total),
        "banned_attempts": dets.filter(status=R.BANNED).count(),
        "sessions": sorted(per.values(), key=lambda r: r["name"]), "daily_imposters": daily,
    }
