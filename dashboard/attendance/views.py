import csv
import hmac
import json
import uuid
from datetime import datetime

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .forms import MemberForm, SessionForm, SettingsForm
from .models import AppSettings, AttendanceRecord, Member, Notification, Session
from .services import analytics, chain, detection, face, gallery, notify, rules


# ---------------------------------------------------------------- dashboard
def _dashboard_ctx():
    now = timezone.now()
    today = timezone.localdate(now)
    todays = AttendanceRecord.objects.filter(timestamp__date=today)
    present_ids = set(todays.filter(status="PRESENT", in_session=True).values_list("member_id", flat=True))
    expected = Member.objects.filter(status=Member.ACTIVE, session__weekday=today.weekday(), session__is_active=True)
    metrics = [
        {"key": "present", "label": "Present", "icon": "user-check", "tone": "good",
         "hint": "Seen in their own session", "value": len(present_ids)},
        {"key": "late", "label": "Late", "icon": "clock", "tone": "warning", "hint": "Also counted in Present",
         "value": todays.filter(status="PRESENT", in_session=True, is_late=True).count()},
        {"key": "absent", "label": "Not seen", "icon": "user-x", "tone": "neutral",
         "hint": "Expected today, not seen yet", "value": expected.exclude(id__in=present_ids).count()},
        {"key": "imposters", "label": "Imposters", "icon": "shield-alert", "tone": "critical",
         "hint": "Faces that matched no member", "value": todays.filter(status="UNKNOWN").count()},
    ]
    peak = max(m["value"] for m in metrics) or 1
    for m in metrics:
        m["pct"] = round(100 * m["value"] / peak)
    unread = Notification.objects.filter(is_read=False)
    return {
        "metrics": metrics,
        "total_members": Member.objects.filter(status=Member.ACTIVE).count(),
        "banned_members": Member.objects.filter(status=Member.BANNED).count(),
        "recent": AttendanceRecord.objects.select_related("member", "session")[:10],
        "alerts": unread[:5],
        "unread_alerts": unread.count(),
        # cached because the page refreshes every few seconds and this call reaches out to Ganache
        "chain_online": cache.get_or_set("chain_online", chain.chain_status, 30),
    }


@login_required
def dashboard(request):
    return render(request, "attendance/dashboard.html", _dashboard_ctx())


@login_required
def dashboard_live(request):
    """Polled by the dashboard so the graph, alerts and activity table stay current."""
    ctx = _dashboard_ctx()
    return JsonResponse({
        "metrics": [{"key": m["key"], "value": m["value"], "pct": m["pct"]} for m in ctx["metrics"]],
        "total_members": ctx["total_members"],
        "banned_members": ctx["banned_members"],
        "unread_alerts": ctx["unread_alerts"],
        "chain_online": ctx["chain_online"],
        "alerts_html": render_to_string("attendance/_alerts.html", ctx),
        "rows_html": render_to_string("attendance/_activity_rows.html", ctx),
    })


# --------------------------------------------------------------- attendance
def _filtered_records(request):
    qs = AttendanceRecord.objects.select_related("member", "session")
    g = request.GET
    if g.get("date"):
        qs = qs.filter(timestamp__date=g["date"])
    if g.get("status"):
        qs = qs.filter(status=g["status"])
    if g.get("session"):
        qs = qs.filter(session_id=g["session"])
    if g.get("q"):
        qs = qs.filter(Q(member__full_name__icontains=g["q"]) | Q(member__member_id__icontains=g["q"]))
    return qs


@login_required
def attendance_list(request):
    return render(request, "attendance/attendance_list.html", {
        "records": _filtered_records(request)[:300], "sessions": Session.objects.all()})


@login_required
def export_csv(request):
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="attendance.csv"'
    w = csv.writer(resp)
    w.writerow(["Timestamp", "Member ID", "Name", "Session", "In own session", "Status", "Late", "Minutes late",
                "Confidence", "Tx hash", "Block"])
    for r in _filtered_records(request):
        w.writerow([timezone.localtime(r.timestamp).isoformat(timespec="seconds"),
                    r.member.member_id if r.member else "", r.member.full_name if r.member else "Unknown",
                    r.session.name if r.session else "", "yes" if r.in_session else "no", r.status,
                    "yes" if r.is_late else "no", r.minutes_late, r.confidence or "", r.tx_hash, r.block_number or ""])
    return resp


# ------------------------------------------------------------------ members
@login_required
def member_list(request):
    members = Member.objects.select_related("session").annotate(
        visits=Count("records", filter=Q(records__status="PRESENT", records__in_session=True)))
    return render(request, "attendance/member_list.html", {"members": members})


@login_required
def member_add(request):
    form = MemberForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        m = form.save()
        messages.success(request, "Details saved. Now scan their face.")
        return redirect("member_scan", pk=m.pk)
    return render(request, "attendance/form.html", {"form": form, "title": "Register member"})


@login_required
def member_edit(request, pk):
    m = get_object_or_404(Member, pk=pk)
    form = MemberForm(request.POST or None, instance=m)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("member_list")
    return render(request, "attendance/form.html", {"form": form, "title": f"Edit {m.full_name}"})


@login_required
def member_scan(request, pk):
    return render(request, "attendance/member_scan.html", {"member": get_object_or_404(Member, pk=pk)})


@login_required
@require_POST
def member_scan_api(request, pk):
    member = get_object_or_404(Member, pk=pk)
    try:
        frames = json.loads(request.body).get("frames", [])
        model, embeddings = face.embeddings_from_data_urls(frames)
    except (ValueError, face.FaceError) as e:
        return JsonResponse({"error": str(e)}, status=400)
    member.embeddings, member.embedding_model = embeddings, model
    member.face_registered_at = timezone.now()
    member.save(update_fields=["embeddings", "embedding_model", "face_registered_at"])
    return JsonResponse({"ok": True, "samples": len(embeddings)})


@login_required
@require_POST
def member_ban(request, pk):
    m = get_object_or_404(Member, pk=pk)
    m.status, m.banned_at = Member.BANNED, timezone.now()
    m.ban_reason = request.POST.get("reason", "")[:200]
    m.save(update_fields=["status", "banned_at", "ban_reason"])
    messages.success(request, f"{m.full_name} is banned. They stay in the recognition set so they can be flagged.")
    return redirect("member_list")


@login_required
@require_POST
def member_unban(request, pk):
    m = get_object_or_404(Member, pk=pk)
    m.status, m.banned_at, m.ban_reason = Member.ACTIVE, None, ""
    m.save(update_fields=["status", "banned_at", "ban_reason"])
    messages.success(request, f"{m.full_name} is active again.")
    return redirect("member_list")


# ----------------------------------------------------------------- sessions
@login_required
def session_list(request):
    return render(request, "attendance/session_list.html",
                  {"sessions": Session.objects.annotate(n=Count("members"))})


@login_required
def session_edit(request, pk=None):
    obj = get_object_or_404(Session, pk=pk) if pk else None
    form = SessionForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("session_list")
    return render(request, "attendance/form.html", {"form": form, "title": "Edit session" if obj else "New session"})


# ------------------------------------------------------------ notifications
@login_required
def notification_list(request):
    return render(request, "attendance/notifications.html", {"items": Notification.objects.select_related("member")[:100]})


@login_required
@require_POST
def notifications_read(request):
    Notification.objects.filter(is_read=False).update(is_read=True)
    return redirect("notifications")


@login_required
def notifications_poll(request):
    """Polled every few seconds by the page to show live pop-ups."""
    if cache.add("inactivity_checked", 1, 600):  # at most every 10 minutes
        notify.check_inactivity()
    latest = Notification.objects.order_by("-id").values_list("id", flat=True).first() or 0
    items = []
    after = request.GET.get("after")
    if after is not None:
        for n in Notification.objects.filter(id__gt=int(after or 0)).order_by("id")[:10]:
            items.append({"id": n.id, "level": n.level, "title": n.get_kind_display(), "message": n.message})
    return JsonResponse({"unread": Notification.objects.filter(is_read=False).count(), "latest": latest, "items": items})


# ----------------------------------------------------------------- settings
@login_required
def settings_page(request):
    form = SettingsForm(request.POST or None, instance=AppSettings.load())
    if request.method == "POST" and form.is_valid():
        # save only the form's fields, so a stale gallery_version is never written back
        form.save(commit=False).save(update_fields=list(form.cleaned_data))
        messages.success(request, "Settings saved.")
        return redirect("settings")
    days = int(request.GET.get("days", 30)) if request.GET.get("days", "30").isdigit() else 30
    return render(request, "attendance/settings.html", {"form": form, "a": analytics.compute(days), "days": days})


@login_required
def chain_view(request):
    on_chain = chain.read_chain_records()
    db_hashes = set(AttendanceRecord.objects.exclude(tx_hash="").values_list("tx_hash", flat=True))
    for r in on_chain:
        r["in_db"] = r["tx_hash"] in db_hashes
    return render(request, "attendance/chain.html", {"online": chain.chain_status(), "records": on_chain})


# ------------------------------------- API for the matcher service (API_CONTRACT.md section 2)
def _token_ok(request):
    return hmac.compare_digest(request.headers.get("Authorization", ""), f"Token {settings.API_TOKEN}")


def _unauthorised():
    return JsonResponse({"error": "unauthorised"}, status=401)


@require_GET
def api_sessions_active(request):
    """Sessions running now, plus gallery_version so the matcher knows when to re-download faces."""
    if not _token_ok(request):
        return _unauthorised()
    now = timezone.now()
    today = timezone.localdate(now)
    sessions = []
    for s in rules.running_sessions(now):  # same rule the dashboard uses, early window included
        start, end = rules.window(s, today)
        sessions.append({"id": s.id, "name": s.name, "starts_at": start.isoformat(), "ends_at": end.isoformat()})
    return JsonResponse({"gallery_version": gallery.current(), "sessions": sessions})


@require_GET
def api_embeddings(request):
    """?session=7,9 -> those sessions' members plus every banned member.  ?scope=all -> everyone with a face."""
    if not _token_ok(request):
        return _unauthorised()
    version = gallery.current()  # read first: a change mid-request just triggers one more refresh
    qs = Member.objects.exclude(embeddings=[]).filter(embedding_model=settings.FACE_MODEL)
    if request.GET.get("scope") == "all":
        pass
    elif request.GET.get("session"):
        try:
            ids = [int(x) for x in request.GET["session"].split(",") if x.strip()]
        except ValueError:
            return JsonResponse({"error": "session must be comma-separated ids"}, status=400)
        qs = qs.filter(Q(session_id__in=ids) | Q(status=Member.BANNED))  # banned are always included
    else:
        return JsonResponse({"error": "pass ?session=<ids> or ?scope=all"}, status=400)
    members = [{"member_id": m.member_id, "banned": m.status == Member.BANNED, "embeddings": m.embeddings}
               for m in qs.only("member_id", "status", "embeddings")]  # no names or emails leave Django
    return JsonResponse({"model": settings.FACE_MODEL, "gallery_version": version, "members": members})


@csrf_exempt
@require_POST
def api_record_attendance(request):
    """One event per face the matcher saw. Django decides present, late, banned or imposter."""
    if not _token_ok(request):
        return _unauthorised()
    try:
        data = json.loads(request.body or "{}")
        captured = datetime.fromisoformat(data["captured_at"])
        confidence = float(data["confidence"])
        camera_id = str(data["camera_id"])[:50]
        event_id = uuid.UUID(str(data["event_id"])) if data.get("event_id") else None
        member_id = None if data.get("member_id") is None else str(data["member_id"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return JsonResponse({"error": "invalid payload"}, status=400)  # the matcher keeps it as dead, not lost
    if captured.tzinfo is None:
        return JsonResponse({"error": "captured_at needs a UTC offset"}, status=400)

    if event_id and AttendanceRecord.objects.filter(event_id=event_id).exists():
        return JsonResponse({"status": "already_seen"})  # a retry of an event we already stored
    try:
        with transaction.atomic():
            # judged at capture time, so events queued during an outage keep the right late/in-session result
            result = detection.process_detection(member_id, confidence, data.get("tx_hash") or "",
                                                 data.get("block_number"), now=captured,
                                                 event_id=event_id, camera_id=camera_id)
    except IntegrityError:  # two retries of the same event raced
        return JsonResponse({"status": "already_seen"})

    body = {"status": result["result"]}  # the matcher's blockchain hook looks for status == "recorded"
    if "id" in result:
        body["record"] = {"id": result["id"], "member_id": member_id, "session_id": result.get("session_id"),
                          "camera_id": camera_id, "captured_at": data["captured_at"]}
    if result["result"] == "recorded":
        body["outside_session"] = not result["in_session"]
    return JsonResponse(body, status=201 if result["result"] in ("recorded", "imposter", "banned") else 200)


@csrf_exempt
def api_member_face(request, member_id):
    """GET: does the member exist?   POST {"model": ..., "embeddings": [[...128 floats], ...]}: save the face.
    Command-line fallback for registration; the model string must match so incompatible vectors can't get in."""
    if not _token_ok(request):
        return _unauthorised()
    member = Member.objects.filter(member_id=member_id).first()
    if not member:
        return JsonResponse({"error": "member not found"}, status=404)
    if request.method == "POST":
        try:
            body = json.loads(request.body or "{}")
            model, emb = body.get("model"), body.get("embeddings", [])
            valid = bool(emb) and all(len(v) == 128 for v in emb)
        except (ValueError, TypeError, AttributeError):
            model, valid = None, False
        if model != settings.FACE_MODEL:
            return JsonResponse({"error": f"model must be {settings.FACE_MODEL}"}, status=400)
        if not valid:
            return JsonResponse({"error": "embeddings must be a list of 128-float vectors"}, status=400)
        member.embeddings, member.embedding_model = emb, settings.FACE_MODEL
        member.face_registered_at = timezone.now()
        member.save(update_fields=["embeddings", "embedding_model", "face_registered_at"])
    return JsonResponse({"member_id": member.member_id, "full_name": member.full_name,
                         "face_registered": member.face_registered})
