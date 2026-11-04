"""One function the camera script calls for every face it sees. All business rules live here."""
from datetime import timedelta

from django.utils import timezone

from ..models import AttendanceRecord as R
from ..models import Member, Notification
from . import notify, rules


def process_detection(member_id, confidence=None, tx_hash="", block_number=None, now=None):
    now = now or timezone.now()
    base = dict(confidence=confidence, tx_hash=tx_hash or "", block_number=block_number, timestamp=now)
    member = Member.objects.filter(member_id=member_id).select_related("session").first() if member_id else None

    # 1. Unregistered face = imposter
    if member is None:
        running = rules.running_sessions(now)
        rec = R.objects.create(status=R.UNKNOWN, in_session=bool(running), **base)
        if running:
            notify.notify(Notification.IMPOSTER,
                          f"Unregistered person detected during {running[0].name}.", dedupe_minutes=2, now=now)
        notify.check_imposter_spike(now)
        return {"result": "imposter", "id": rec.id}

    info = rules.classify(member, now)

    # 2. Banned member
    if member.status == Member.BANNED:
        rec = R.objects.create(member=member, status=R.BANNED, session=info["session"],
                               in_session=info["in_session"], **base)
        where = "during their session" if info["in_session"] else "outside their session"
        notify.notify(Notification.BANNED_IN_SESSION,
                      f"Banned member {member.full_name} ({member.member_id}) detected {where}.",
                      member=member, dedupe_minutes=10, now=now)
        return {"result": "banned", "id": rec.id}

    # 3. Normal attendance, one per session-day (or one per hour if outside their session)
    mine = R.objects.filter(member=member, status=R.PRESENT)
    if info["in_session"]:
        if mine.filter(in_session=True, timestamp__date=timezone.localdate(now)).exists():
            return {"result": "already_marked"}
    elif mine.filter(timestamp__gte=now - timedelta(hours=1)).exists():
        return {"result": "already_seen"}

    rec = R.objects.create(member=member, status=R.PRESENT, session=info["session"], in_session=info["in_session"],
                           minutes_late=info["minutes_late"], is_late=info["is_late"], **base)
    return {"result": "recorded", "id": rec.id, "in_session": rec.in_session, "late": rec.is_late}
