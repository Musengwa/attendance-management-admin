from django.contrib import admin

from .models import AppSettings, AttendanceRecord, Member, Notification, Session, SessionSlot

admin.site.register([AppSettings, Notification])


class SessionSlotInline(admin.TabularInline):
    model = SessionSlot
    extra = 0


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("name", "schedule_summary", "is_active")
    inlines = [SessionSlotInline]


@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("member_id", "full_name", "session", "status")
    search_fields = ("member_id", "full_name")
    exclude = ("embeddings",)


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "member", "status", "in_session", "is_late")
    list_filter = ("status", "in_session")
