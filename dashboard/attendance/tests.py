import json
from datetime import datetime, time, timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import AppSettings, AttendanceRecord as R, Member, Notification, Session
from .services import analytics, detection, notify

# Monday 2 Nov 2026, local time
def at(h, m=0, day=2):
    return timezone.make_aware(datetime(2026, 11, day, h, m))


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.session = Session.objects.create(name="Maths", weekday=0, start_time=time(9), end_time=time(11))
        self.m = Member.objects.create(member_id="1", full_name="Ann", session=self.session,
                                       registered_at=at(8, day=2) - timedelta(days=30), embeddings=[[0.1] * 128])


class RuleTests(Base):
    def test_on_time(self):
        r = detection.process_detection("1", 0.8, now=at(9, 5))
        self.assertEqual(r["result"], "recorded")
        self.assertTrue(R.objects.get().in_session)
        self.assertFalse(R.objects.get().is_late)

    def test_late(self):
        detection.process_detection("1", 0.8, now=at(9, 30))
        rec = R.objects.get()
        self.assertTrue(rec.is_late)
        self.assertEqual(rec.minutes_late, 30)

    def test_early_window_counts_as_in_session(self):
        detection.process_detection("1", now=at(8, 50))
        self.assertTrue(R.objects.get().in_session)

    def test_outside_session(self):
        detection.process_detection("1", now=at(14))
        self.assertFalse(R.objects.get().in_session)

    def test_wrong_day_is_outside_session(self):
        detection.process_detection("1", now=at(9, 30, day=3))  # Tuesday
        self.assertFalse(R.objects.get().in_session)

    def test_only_once_per_session(self):
        detection.process_detection("1", now=at(9, 5))
        self.assertEqual(detection.process_detection("1", now=at(9, 40))["result"], "already_marked")

    def test_edited_session_time_is_respected(self):
        self.session.start_time, self.session.end_time = time(13), time(15)
        self.session.save()
        detection.process_detection("1", now=at(9, 30))
        self.assertFalse(R.objects.get().in_session)


class NotificationTests(Base):
    def test_banned_member_alert(self):
        self.m.status = Member.BANNED
        self.m.save()
        detection.process_detection("1", now=at(9, 30))
        self.assertEqual(R.objects.get().status, R.BANNED)
        n = Notification.objects.get()
        self.assertEqual(n.kind, Notification.BANNED_IN_SESSION)
        self.assertIn("during their session", n.message)

    def test_imposter_during_session_alerts(self):
        detection.process_detection(None, now=at(9, 30))
        self.assertEqual(Notification.objects.filter(kind=Notification.IMPOSTER).count(), 1)

    def test_imposter_outside_any_session_is_recorded_but_silent(self):
        detection.process_detection(None, now=at(15))
        self.assertEqual(R.objects.filter(status=R.UNKNOWN).count(), 1)
        self.assertEqual(Notification.objects.count(), 0)

    def test_imposter_alerts_are_deduplicated(self):
        detection.process_detection(None, now=at(9, 30))
        detection.process_detection(None, now=at(9, 31))
        self.assertEqual(Notification.objects.filter(kind=Notification.IMPOSTER).count(), 1)

    def test_imposter_spike(self):
        cfg = AppSettings.load()
        for i in range(cfg.imposter_spike_count):
            detection.process_detection(None, now=at(9, 30) + timedelta(minutes=i))
        self.assertTrue(Notification.objects.filter(kind=Notification.IMPOSTER_SPIKE).exists())

    def test_inactivity_alert_after_n_missed_sessions(self):
        notify.check_inactivity(now=at(12))  # Mondays Oct 19, 26, Nov 2 all finished, no attendance
        self.assertEqual(Notification.objects.filter(kind=Notification.INACTIVE, member=self.m).count(), 1)
        notify.check_inactivity(now=at(12))  # not repeated
        self.assertEqual(Notification.objects.filter(kind=Notification.INACTIVE).count(), 1)

    def test_no_inactivity_alert_if_attended_recently(self):
        detection.process_detection("1", now=at(9, 5, day=2) - timedelta(days=7))
        notify.check_inactivity(now=at(12))
        self.assertFalse(Notification.objects.filter(kind=Notification.INACTIVE).exists())

    def test_new_member_not_flagged_inactive(self):
        self.m.registered_at = at(8)
        self.m.save()
        notify.check_inactivity(now=at(12))
        self.assertFalse(Notification.objects.exists())


class AnalyticsTests(Base):
    def test_rates(self):
        Member.objects.create(member_id="2", full_name="Bob", session=self.session, registered_at=at(8) - timedelta(days=30))
        detection.process_detection("1", now=at(9, 30))   # Ann late, present
        detection.process_detection(None, now=at(9, 40))  # imposter
        a = analytics.compute(days=1, now=at(12))
        self.assertEqual((a["expected"], a["present"], a["late"]), (2, 1, 1))
        self.assertEqual(a["attendance_rate"], 50.0)
        self.assertEqual(a["tardiness_rate"], 100.0)
        self.assertEqual(a["imposter_rate"], 50.0)


@override_settings(API_TOKEN="t")
class ApiTests(Base):
    h = {"HTTP_AUTHORIZATION": "Token t"}

    def post(self, payload, token="t"):
        return self.client.post("/api/attendance/", json.dumps(payload), content_type="application/json",
                                HTTP_AUTHORIZATION=f"Token {token}")

    def test_bad_token(self):
        self.assertEqual(self.post({"member_id": "1"}, token="x").status_code, 401)

    def test_unknown_member_id_is_imposter(self):
        self.assertEqual(self.post({"member_id": "999"}).json()["result"], "imposter")

    def test_embeddings_include_banned_members(self):
        self.m.status = Member.BANNED
        self.m.save()
        d = self.client.get("/api/embeddings/", **self.h).json()
        self.assertIn("1", d)

    def test_face_upload_validates_shape(self):
        bad = self.client.post("/api/members/1/face/", json.dumps({"embeddings": [[1, 2]]}),
                               content_type="application/json", **self.h)
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post("/api/members/1/face/", json.dumps({"embeddings": [[0.5] * 128]}),
                              content_type="application/json", **self.h)
        self.assertTrue(ok.json()["face_registered"])
        self.assertEqual(self.client.get("/api/members/99/face/", **self.h).status_code, 404)


class ViewTests(Base):
    def setUp(self):
        super().setUp()
        User.objects.create_user("a", password="p")
        self.client.login(username="a", password="p")

    def test_pages_load(self):
        for name in ["/", "/attendance/", "/members/", "/members/add/", "/sessions/", "/sessions/add/",
                     "/notifications/", "/settings/", "/settings/?days=7", "/chain/", f"/members/{self.m.pk}/scan/",
                     f"/members/{self.m.pk}/edit/", f"/sessions/{self.session.pk}/edit/", "/attendance/export.csv"]:
            self.assertEqual(self.client.get(name).status_code, 200, name)

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.client.get("/dashboard/live/").status_code, 302)

    def test_dashboard_live(self):
        R.objects.create(member=None, status="UNKNOWN", in_session=True, confidence=0.1)
        d = self.client.get("/dashboard/live/").json()
        metrics = {m["key"]: m for m in d["metrics"]}
        self.assertEqual(set(metrics), {"present", "late", "absent", "imposters"})
        self.assertEqual(metrics["imposters"]["value"], 1)
        self.assertEqual(max(m["pct"] for m in d["metrics"]), 100)
        self.assertIn("data-id=", d["rows_html"])

    def test_ban_and_unban(self):
        self.client.post(f"/members/{self.m.pk}/ban/", {"reason": "Fighting"})
        self.m.refresh_from_db()
        self.assertEqual((self.m.status, self.m.ban_reason), ("BANNED", "Fighting"))
        self.client.post(f"/members/{self.m.pk}/unban/")
        self.m.refresh_from_db()
        self.assertEqual(self.m.status, "ACTIVE")

    def test_add_member_redirects_to_scan(self):
        r = self.client.post("/members/add/", {"member_id": "7", "full_name": "Zed", "email": "z@x.com",
                                               "session": self.session.pk})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/scan/", r["Location"])

    def test_session_validation(self):
        r = self.client.post("/sessions/add/", {"name": "X", "weekday": 1, "start_time": "10:00", "end_time": "09:00"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Session.objects.filter(name="X").count(), 0)

    def test_scan_saves_embeddings(self):
        with mock.patch("attendance.services.face.embeddings_from_data_urls", return_value=[[0.2] * 128] * 6):
            r = self.client.post(f"/members/{self.m.pk}/scan/upload/", json.dumps({"frames": ["x"]}),
                                 content_type="application/json")
        self.assertEqual(r.json()["samples"], 6)

    def test_poll_returns_new_notifications_only_after_first_call(self):
        notify.notify("IMPOSTER", "old")
        first = self.client.get("/notifications/poll/").json()
        self.assertEqual(first["items"], [])
        notify.notify("IMPOSTER", "new")
        second = self.client.get(f"/notifications/poll/?after={first['latest']}").json()
        self.assertEqual([i["message"] for i in second["items"]], ["new"])
