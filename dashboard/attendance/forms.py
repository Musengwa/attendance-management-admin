from django import forms
from django.forms import inlineformset_factory

from .models import WEEKDAYS, AppSettings, Member, Session, SessionSlot


class MemberForm(forms.ModelForm):
    class Meta:
        model = Member
        fields = ["member_id", "full_name", "email", "session"]


class SessionForm(forms.ModelForm):
    class Meta:
        model = Session
        fields = ["name", "is_active"]


class SessionSlotForm(forms.ModelForm):
    """One timetable line: weekly on some days, or once on a date."""
    WEEKLY, ONCE = "weekly", "once"
    kind = forms.ChoiceField(choices=[(WEEKLY, "Every week on"), (ONCE, "Once, on")], initial=WEEKLY)
    days = forms.TypedMultipleChoiceField(choices=[(d, n[:3]) for d, n in WEEKDAYS], coerce=int, required=False,
                                          widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = SessionSlot
        fields = ["kind", "days", "date", "start_time", "end_time"]
        help_texts = {"days": "", "date": ""}  # the row's layout already says what each part means
        widgets = {"date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "start_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
                   "end_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["kind"] = self.ONCE if self.instance.date else self.WEEKLY

    def clean(self):
        d = super().clean()
        if d.get("kind") == self.ONCE:
            if not d.get("date"):
                self.add_error("date", "Pick the date of this one-off session.")
            d["days"] = []
        else:
            if not d.get("days"):
                self.add_error("days", "Tick at least one day.")
            d["date"] = None
        if d.get("start_time") and d.get("end_time") and d["end_time"] <= d["start_time"]:
            self.add_error("end_time", "End time must be after start time.")
        return d


SessionSlotFormSet = inlineformset_factory(
    Session, SessionSlot, form=SessionSlotForm, extra=0, min_num=1, validate_min=True, can_delete=True)


class SettingsForm(forms.ModelForm):
    class Meta:
        model = AppSettings
        fields = ["late_grace_minutes", "early_window_minutes", "imposter_spike_count",
                  "imposter_spike_minutes", "inactive_sessions_threshold"]
