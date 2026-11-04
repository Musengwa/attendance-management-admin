from django.contrib import admin

from .models import AppSettings, AttendanceRecord, Member, Notification, Session

admin.site.register([AppSettings, Session, Notification])


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("member_id", "full_name", "session", "status")
    search_fields = ("member_id", "full_name")
    exclude = ("embeddings",)


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "member", "status", "in_session", "is_late")
    list_filter = ("status", "in_session")
