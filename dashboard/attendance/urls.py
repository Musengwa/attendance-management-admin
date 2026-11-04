from django.urls import path

from . import views as v

urlpatterns = [
    path("", v.dashboard, name="dashboard"),
    path("attendance/", v.attendance_list, name="attendance_list"),
    path("attendance/export.csv", v.export_csv, name="export_csv"),
    path("members/", v.member_list, name="member_list"),
    path("members/add/", v.member_add, name="member_add"),
    path("members/<int:pk>/edit/", v.member_edit, name="member_edit"),
    path("members/<int:pk>/scan/", v.member_scan, name="member_scan"),
    path("members/<int:pk>/scan/upload/", v.member_scan_api, name="member_scan_api"),
    path("members/<int:pk>/ban/", v.member_ban, name="member_ban"),
    path("members/<int:pk>/unban/", v.member_unban, name="member_unban"),
    path("sessions/", v.session_list, name="session_list"),
    path("sessions/add/", v.session_edit, name="session_add"),
    path("sessions/<int:pk>/edit/", v.session_edit, name="session_edit"),
    path("notifications/", v.notification_list, name="notifications"),
    path("notifications/read/", v.notifications_read, name="notifications_read"),
    path("notifications/poll/", v.notifications_poll, name="notifications_poll"),
    path("settings/", v.settings_page, name="settings"),
    path("chain/", v.chain_view, name="chain"),
    path("api/attendance/", v.api_record_attendance, name="api_attendance"),
    path("api/members/<str:member_id>/face/", v.api_member_face, name="api_member_face"),
    path("api/embeddings/", v.api_embeddings, name="api_embeddings"),
]
