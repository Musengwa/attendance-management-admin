import json
import uuid
from datetime import datetime, time, timedelta
from unittest import mock

import httpx
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import AppSettings, AttendanceRecord as R, Member, Notification, Session
from .services import analytics, detection, face, gallery, notify

# Monday 2 Nov 2026, local time
def at(h, m=0, day=2):
    return timezone.make_aware(datetime(2026, 11, day, h, m))


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.session = Session.objects.create(name="Maths", weekday=0, start_time=time(9), end_time=time(11))
        self.m = Member.objects.create(member_id="1", full_name="Ann", session=self.session,
                                       registered_at=at(8, day=2) - timedelta(days=30), embeddings=[[0.1] * 128],
                                       embedding_model=settings.FACE_MODEL)


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

    def ev(self, **kw):
        """An event shaped like the matcher's (API_CONTRACT 2.3)."""
        return {"member_id": "1", "confidence": 0.7, "camera_id": "door-1", "captured_at": at(9, 5).isoformat(),
                "tx_hash": "", "block_number": None, "event_id": str(uuid.uuid4()), **kw}

    def get(self, url):
        return self.client.get(url, **self.h)

    def test_bad_token(self):
        self.assertEqual(self.post(self.ev(), token="x").status_code, 401)
        self.assertEqual(self.client.get("/api/sessions/active/", HTTP_AUTHORIZATION="Token x").status_code, 401)
        self.assertEqual(self.client.get("/api/embeddings/?scope=all").status_code, 401)

    def test_unknown_member_id_is_imposter(self):
        self.assertEqual(self.post(self.ev(member_id="999")).json()["status"], "imposter")

    def test_attendance_statuses_match_contract(self):
        banned = Member.objects.create(member_id="3", full_name="Cy", status=Member.BANNED)
        cases = [
            (self.ev(), 201, "recorded"),
            (self.ev(captured_at=at(9, 20).isoformat()), 200, "already_marked"),
            (self.ev(member_id=None, confidence=0.2), 201, "imposter"),
            (self.ev(member_id=banned.member_id), 201, "banned"),
        ]
        for payload, code, status in cases:
            r = self.post(payload)
            self.assertEqual((r.status_code, r.json()["status"]), (code, status), payload)
        body = self.post(self.ev(captured_at=at(14).isoformat(), member_id="1", event_id=None)).json()
        self.assertEqual(body["status"], "recorded")
        self.assertTrue(body["outside_session"])

    def test_recorded_reply_carries_record(self):
        body = self.post(self.ev()).json()
        self.assertFalse(body["outside_session"])
        rec = R.objects.get()
        self.assertEqual(body["record"], {"id": rec.id, "member_id": "1", "session_id": self.session.id,
                                          "camera_id": "door-1", "captured_at": at(9, 5).isoformat()})
        self.assertEqual((rec.camera_id, rec.timestamp), ("door-1", at(9, 5)))

    def test_same_event_id_twice_is_stored_once(self):
        payload = self.ev(member_id=None, confidence=0.2)
        self.assertEqual(self.post(payload).status_code, 201)
        r = self.post(payload)
        self.assertEqual((r.status_code, r.json()["status"]), (200, "already_seen"))
        self.assertEqual(R.objects.count(), 1)

    def test_late_arriving_event_is_judged_at_capture_time(self):
        # captured on time during the session; the request itself arrives days later
        self.post(self.ev(captured_at=at(9, 5).isoformat()))
        rec = R.objects.get()
        self.assertTrue(rec.in_session)
        self.assertFalse(rec.is_late)

    def test_replayed_event_outside_session_is_deduplicated_both_ways(self):
        self.post(self.ev(captured_at=at(14, 30).isoformat()))
        self.assertEqual(self.post(self.ev(captured_at=at(14).isoformat())).json()["status"], "already_seen")

    def test_bad_payloads_are_400(self):
        for bad in [{}, self.ev(captured_at="not a time"), self.ev(captured_at="2026-11-02T09:05:00"),
                    self.ev(confidence=None), self.ev(event_id="nope"), [1, 2]]:
            self.assertEqual(self.post(bad).status_code, 400, bad)
        missing_camera = self.ev()
        del missing_camera["camera_id"]
        self.assertEqual(self.post(missing_camera).status_code, 400)

    def test_sessions_active(self):
        with mock.patch("django.utils.timezone.now", return_value=at(9, 30)):
            d = self.get("/api/sessions/active/").json()
        self.assertEqual(d["sessions"], [{"id": self.session.id, "name": "Maths",
                                          "starts_at": at(9).isoformat(), "ends_at": at(11).isoformat()}])
        self.assertIsInstance(d["gallery_version"], str)
        with mock.patch("django.utils.timezone.now", return_value=at(14)):
            self.assertEqual(self.get("/api/sessions/active/").json()["sessions"], [])

    def test_gallery_version_changes_on_member_session_and_ban_changes(self):
        v = gallery.current()
        self.m.full_name = "Ann B"
        self.m.save()
        self.assertNotEqual(gallery.current(), v)
        v = gallery.current()
        self.session.end_time = time(12)
        self.session.save()
        self.assertNotEqual(gallery.current(), v)

    def test_settings_page_does_not_reset_gallery_version(self):
        User.objects.create_user("a", password="p")
        self.client.login(username="a", password="p")
        gallery.bump()
        v = gallery.current()
        self.client.post("/settings/", {"late_grace_minutes": 5, "early_window_minutes": 15,
                                        "imposter_spike_count": 5, "imposter_spike_minutes": 30,
                                        "inactive_sessions_threshold": 3})
        self.assertEqual(AppSettings.load().late_grace_minutes, 5)
        self.assertEqual(gallery.current(), v)

    def test_embeddings_filters(self):
        other = Session.objects.create(name="Art", weekday=1, start_time=time(9), end_time=time(10))
        Member.objects.create(member_id="2", full_name="Bo", session=other, embeddings=[[0.2] * 128],
                              embedding_model=settings.FACE_MODEL)
        Member.objects.create(member_id="3", full_name="Cy", status=Member.BANNED, embeddings=[[0.3] * 128],
                              embedding_model=settings.FACE_MODEL)
        Member.objects.create(member_id="4", full_name="Old", session=self.session, embeddings=[[0.4] * 128])
        Member.objects.create(member_id="5", full_name="No face", session=self.session)

        d = self.get(f"/api/embeddings/?session={self.session.id}").json()
        self.assertEqual(d["model"], settings.FACE_MODEL)
        self.assertEqual({m["member_id"]: m["banned"] for m in d["members"]}, {"1": False, "3": True})
        self.assertNotIn("full_name", d["members"][0])
        everyone = self.get("/api/embeddings/?scope=all").json()
        self.assertEqual(sorted(m["member_id"] for m in everyone["members"]), ["1", "2", "3"])  # no legacy, no faceless
        self.assertEqual(self.get("/api/embeddings/").status_code, 400)
        self.assertEqual(self.get("/api/embeddings/?session=x").status_code, 400)

    def test_embeddings_include_banned_members(self):
        self.m.status = Member.BANNED
        self.m.save()
        d = self.get("/api/embeddings/?session=999").json()
        self.assertIn("1", [m["member_id"] for m in d["members"]])

    def test_face_upload_validates_shape_and_model(self):
        good = {"model": settings.FACE_MODEL, "embeddings": [[0.5] * 128]}
        for bad in [{**good, "embeddings": [[1, 2]]}, {**good, "model": "other"}, {"embeddings": [[0.5] * 128]}]:
            r = self.client.post("/api/members/1/face/", json.dumps(bad), content_type="application/json", **self.h)
            self.assertEqual(r.status_code, 400, bad)
        ok = self.client.post("/api/members/1/face/", json.dumps(good), content_type="application/json", **self.h)
        self.assertTrue(ok.json()["face_registered"])
        self.assertEqual(self.get("/api/members/99/face/").status_code, 404)


@override_settings(EMBED_URL="http://embed.test", EMBED_API_TOKEN="e")
class FaceServiceTests(TestCase):
    def reply(self, code, body):
        return httpx.Response(code, json=body, request=httpx.Request("POST", "http://embed.test/v1/embed/batch"))

    def test_success_returns_model_and_embeddings(self):
        body = {"model": settings.FACE_MODEL, "total": 6, "usable": 6, "min_usable": 5,
                "embeddings": [[0.1] * 128] * 6, "rejected": []}
        with mock.patch("httpx.post", return_value=self.reply(200, body)) as post:
            model, emb = face.embeddings_from_data_urls(["data:image/jpeg;base64,AAAA"] * 40)
        self.assertEqual((model, len(emb)), (settings.FACE_MODEL, 6))
        sent = post.call_args.kwargs
        self.assertEqual(len(sent["json"]["images_b64"]), face.MAX_FRAMES)
        self.assertEqual(sent["headers"]["Authorization"], "Token e")

    def test_422_explains_rejections(self):
        body = {"model": settings.FACE_MODEL, "total": 14, "usable": 3, "min_usable": 5, "embeddings": [],
                "rejected": [{"index": 0, "reason": "blurry"}, {"index": 1, "reason": "blurry"},
                             {"index": 2, "reason": "multiple_faces"}]}
        with mock.patch("httpx.post", return_value=self.reply(422, body)):
            with self.assertRaisesRegex(face.FaceError,
                                        r"Only 3 of 14 .*at least 5 are needed \(2 blurry, 1 more than one face\)"):
                face.embeddings_from_data_urls(["x"])

    def test_wrong_model_is_refused(self):
        body = {"model": "something-else", "embeddings": [[0.1] * 128] * 5}
        with mock.patch("httpx.post", return_value=self.reply(200, body)):
            with self.assertRaisesRegex(face.FaceError, "expected"):
                face.embeddings_from_data_urls(["x"])

    def test_service_down(self):
        with mock.patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            with self.assertRaisesRegex(face.FaceError, "not reachable"):
                face.embeddings_from_data_urls(["x"])

    def test_bad_token(self):
        with mock.patch("httpx.post", return_value=self.reply(401, {"error": {"code": "unauthorized"}})):
            with self.assertRaisesRegex(face.FaceError, "EMBED_API_TOKEN"):
                face.embeddings_from_data_urls(["x"])


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
        with mock.patch("attendance.services.face.embeddings_from_data_urls",
                        return_value=(settings.FACE_MODEL, [[0.2] * 128] * 6)):
            r = self.client.post(f"/members/{self.m.pk}/scan/upload/", json.dumps({"frames": ["x"]}),
                                 content_type="application/json")
        self.assertEqual(r.json()["samples"], 6)
        self.m.refresh_from_db()
        self.assertEqual(self.m.embedding_model, settings.FACE_MODEL)

    def test_scan_shows_face_service_errors(self):
        with mock.patch("attendance.services.face.embeddings_from_data_urls",
                        side_effect=face.FaceError("The face service is not reachable.")):
            r = self.client.post(f"/members/{self.m.pk}/scan/upload/", json.dumps({"frames": ["x"]}),
                                 content_type="application/json")
        self.assertEqual((r.status_code, r.json()["error"]), (400, "The face service is not reachable."))

    def test_legacy_embeddings_show_as_not_scanned(self):
        self.m.embedding_model = ""
        self.m.save()
        self.assertFalse(Member.objects.get(pk=self.m.pk).face_registered)

    def test_poll_returns_new_notifications_only_after_first_call(self):
        notify.notify("IMPOSTER", "old")
        first = self.client.get("/notifications/poll/").json()
        self.assertEqual(first["items"], [])
        notify.notify("IMPOSTER", "new")
        second = self.client.get(f"/notifications/poll/?after={first['latest']}").json()
        self.assertEqual([i["message"] for i in second["items"]], ["new"])
