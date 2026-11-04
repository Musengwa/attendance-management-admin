import csv
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import MemberForm, SessionForm, SettingsForm
from .models import AppSettings, AttendanceRecord, Member, Notification, Session
from .services import analytics, chain, detection, face, notify


# ---------------------------------------------------------------- dashboard
@login_required
def dashboard(request):
    now = timezone.now()
    today = timezone.localdate(now)
    todays = AttendanceRecord.objects.filter(timestamp__date=today)
    present_ids = set(todays.filter(status="PRESENT", in_session=True).values_list("member_id", flat=True))
    expected = Member.objects.filter(status=Member.ACTIVE, session__weekday=today.weekday(), session__is_active=True)
    ctx = {
        "total_members": Member.objects.filter(status=Member.ACTIVE).count(),
        "banned_members": Member.objects.filter(status=Member.BANNED).count(),
        "present_today": len(present_ids),
        "late_today": todays.filter(status="PRESENT", in_session=True, is_late=True).count(),
        "absent_today": expected.exclude(id__in=present_ids).count(),
        "imposters_today": todays.filter(status="UNKNOWN").count(),
        "recent": AttendanceRecord.objects.select_related("member", "session")[:10],
        "alerts": Notification.objects.filter(is_read=False)[:5],
        "chain_online": chain.chain_status(),
    }
    return render(request, "attendance/dashboard.html", ctx)


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
        embeddings = face.embeddings_from_data_urls(frames)
    except (ValueError, face.FaceError) as e:
        return JsonResponse({"error": str(e)}, status=400)
    member.embeddings = embeddings
    member.face_registered_at = timezone.now()
    member.save(update_fields=["embeddings", "face_registered_at"])
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
        form.save()
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


# ---------------------------------------------- API for the camera script
def _token_ok(request):
    return request.headers.get("Authorization") == f"Token {settings.API_TOKEN}"


@csrf_exempt
@require_POST
def api_record_attendance(request):
    """Body: {"member_id": "...", "confidence": 0.71, "tx_hash": "0x..", "block_number": 12}; member_id null = unknown."""
    if not _token_ok(request):
        return JsonResponse({"error": "unauthorised"}, status=401)
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid json"}, status=400)
    result = detection.process_detection(data.get("member_id"), data.get("confidence"),
                                         data.get("tx_hash", ""), data.get("block_number"))
    return JsonResponse(result, status=201 if result["result"] in ("recorded", "imposter", "banned") else 200)


@csrf_exempt
def api_member_face(request, member_id):
    """GET: does the member exist?   POST {"embeddings": [[...128 floats], ...]}: save the face."""
    if not _token_ok(request):
        return JsonResponse({"error": "unauthorised"}, status=401)
    member = Member.objects.filter(member_id=member_id).first()
    if not member:
        return JsonResponse({"error": "member not found"}, status=404)
    if request.method == "POST":
        try:
            emb = json.loads(request.body).get("embeddings", [])
        except ValueError:
            emb = []
        if not emb or any(len(v) != 128 for v in emb):
            return JsonResponse({"error": "embeddings must be a list of 128-float vectors"}, status=400)
        member.embeddings, member.face_registered_at = emb, timezone.now()
        member.save(update_fields=["embeddings", "face_registered_at"])
    return JsonResponse({"member_id": member.member_id, "full_name": member.full_name,
                         "face_registered": member.face_registered})


def api_embeddings(request):
    """The camera script downloads every registered face (banned members included, so they can be flagged)."""
    if not _token_ok(request):
        return JsonResponse({"error": "unauthorised"}, status=401)
    data = {m.member_id: m.embeddings for m in Member.objects.exclude(embeddings=[])}
    return JsonResponse(data)
