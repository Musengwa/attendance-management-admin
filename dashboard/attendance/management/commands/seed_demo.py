from datetime import time, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from attendance.models import AttendanceRecord, Member, Session


class Command(BaseCommand):
    help = "Create demo sessions, members and attendance so every dashboard page has something to show"

    def handle(self, *args, **opts):
        now = timezone.now()
        today = timezone.localdate(now)
        s1, _ = Session.objects.get_or_create(name="Software Engineering", weekday=today.weekday(),
                                              defaults={"start_time": time(0, 0), "end_time": time(23, 59)})
        s2, _ = Session.objects.get_or_create(name="Databases", weekday=(today.weekday() + 2) % 7,
                                              defaults={"start_time": time(10), "end_time": time(12)})
        old = now - timedelta(days=30)
        people = [("202400004", "Mubanga Muma", s1), ("202400005", "Chanda Mwila", s1), ("202400006", "Natasha Phiri", s2)]
        for mid, name, s in people:
            Member.objects.get_or_create(member_id=mid, defaults={
                "full_name": name, "session": s, "registered_at": old, "email": f"{mid}@example.com"})
        ann = Member.objects.get(member_id="202400004")
        AttendanceRecord.objects.create(member=ann, session=s1, in_session=True, confidence=0.82)
        AttendanceRecord.objects.create(member=None, status="UNKNOWN", in_session=True, confidence=0.12)
        self.stdout.write(self.style.SUCCESS("Demo data created (members have no face scan yet; scan them on the Members page)."))
