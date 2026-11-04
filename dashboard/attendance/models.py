from django.db import models
from django.utils import timezone

WEEKDAYS = [(0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"),
            (4, "Friday"), (5, "Saturday"), (6, "Sunday")]


class AppSettings(models.Model):
    """Single row holding every threshold the admin can change on the Settings page."""
    late_grace_minutes = models.PositiveIntegerField(default=10, help_text="Minutes after start before someone counts as late")
    early_window_minutes = models.PositiveIntegerField(default=15, help_text="How many minutes before start attendance is accepted")
    imposter_spike_count = models.PositiveIntegerField(default=5, help_text="Unregistered faces needed to trigger a 'many imposters' alert")
    imposter_spike_minutes = models.PositiveIntegerField(default=30, help_text="...within this many minutes")
    inactive_sessions_threshold = models.PositiveIntegerField(default=3, help_text="Consecutive missed sessions before an inactivity alert")

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Session(models.Model):
    """A weekly class/session. Start and end times can be edited at any time."""
    name = models.CharField(max_length=120)
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["weekday", "start_time"]

    def __str__(self):
        return f"{self.name} ({self.get_weekday_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M})"


class Member(models.Model):
    ACTIVE = "ACTIVE"
    BANNED = "BANNED"
    STATUS_CHOICES = [(ACTIVE, "Active"), (BANNED, "Banned")]

    member_id = models.CharField(max_length=50, unique=True)
    full_name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    session = models.ForeignKey(Session, null=True, blank=True, on_delete=models.SET_NULL, related_name="members")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=ACTIVE)
    ban_reason = models.CharField(max_length=200, blank=True)
    banned_at = models.DateTimeField(null=True, blank=True)
    embeddings = models.JSONField(default=list, blank=True)  # list of 128-float SFace vectors (no photos stored)
    face_registered_at = models.DateTimeField(null=True, blank=True)
    registered_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["full_name"]

    @property
    def face_registered(self):
        return bool(self.embeddings)

    def __str__(self):
        return f"{self.full_name} ({self.member_id})"


class AttendanceRecord(models.Model):
    PRESENT, UNKNOWN, BANNED = "PRESENT", "UNKNOWN", "BANNED"
    STATUS_CHOICES = [(PRESENT, "Present"), (UNKNOWN, "Imposter (unregistered)"), (BANNED, "Banned member")]

    member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.SET_NULL, related_name="records")
    session = models.ForeignKey(Session, null=True, blank=True, on_delete=models.SET_NULL, related_name="records")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PRESENT)
    in_session = models.BooleanField(default=False, help_text="Detected while their own session was running")
    minutes_late = models.PositiveIntegerField(default=0)
    is_late = models.BooleanField(default=False)
    confidence = models.FloatField(null=True, blank=True, help_text="Cosine similarity (higher = better match)")
    timestamp = models.DateTimeField(default=timezone.now)
    tx_hash = models.CharField(max_length=80, blank=True)
    block_number = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]

    @property
    def on_chain(self):
        return bool(self.tx_hash)


class Notification(models.Model):
    BANNED_IN_SESSION = "BANNED"
    IMPOSTER = "IMPOSTER"
    IMPOSTER_SPIKE = "SPIKE"
    INACTIVE = "INACTIVE"
    KIND_CHOICES = [(BANNED_IN_SESSION, "Banned user detected"), (IMPOSTER, "Imposter detected"),
                    (IMPOSTER_SPIKE, "Many imposters"), (INACTIVE, "Member inactive")]
    DANGER = {BANNED_IN_SESSION, IMPOSTER, IMPOSTER_SPIKE}

    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    message = models.CharField(max_length=300)
    member = models.ForeignKey(Member, null=True, blank=True, on_delete=models.SET_NULL, related_name="notifications")
    created_at = models.DateTimeField(default=timezone.now)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at", "-id"]

    @property
    def level(self):
        return "danger" if self.kind in self.DANGER else "warn"
