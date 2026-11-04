from django import forms

from .models import AppSettings, Member, Session


class MemberForm(forms.ModelForm):
    class Meta:
        model = Member
        fields = ["member_id", "full_name", "email", "session"]


class SessionForm(forms.ModelForm):
    class Meta:
        model = Session
        fields = ["name", "weekday", "start_time", "end_time", "is_active"]
        widgets = {"start_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
                   "end_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")}

    def clean(self):
        d = super().clean()
        if d.get("start_time") and d.get("end_time") and d["end_time"] <= d["start_time"]:
            raise forms.ValidationError("End time must be after start time.")
        return d


class SettingsForm(forms.ModelForm):
    class Meta:
        model = AppSettings
        fields = ["late_grace_minutes", "early_window_minutes", "imposter_spike_count",
                  "imposter_spike_minutes", "inactive_sessions_threshold"]
