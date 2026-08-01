from django import forms
from django.contrib.auth.models import User
from django.utils import timezone
import datetime

from AuthFit.models import Enrollment, MembershipPlan, Trainer

from .models import Announcement


# Keep this in sync with the cron schedule (see management command below).
PUBLISH_TIME_SLOTS = [
    ('06:00', '6:00 AM'),
    ('12:00', '12:00 PM'),
    ('20:00', '8:00 PM'),
]


class MemberModelMultipleChoiceField(forms.ModelMultipleChoiceField):
    def __init__(self, *args, enrollment_lookup=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._enrollment_lookup = enrollment_lookup or {}

    def label_from_instance(self, user):
        enr = self._enrollment_lookup.get(user.pk)
        if enr:
            return f"{enr.fullname} ({enr.phone})"
        return user.get_full_name() or user.username


class AnnouncementForm(forms.ModelForm):

    target_members = MemberModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': 8}),
    )

    # Replace the raw datetime-local publish_at with date + fixed slot.
    publish_date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        label='Publish Date',
    )
    publish_time = forms.ChoiceField(
        choices=PUBLISH_TIME_SLOTS,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label='Publish Time Slot',
    )

    class Meta:
        model = Announcement
        fields = [
            'title', 'description', 'announcement_type', 'priority',
            'image', 'attachment', 'external_link',
            'expires_at', 'is_active',
            'show_popup', 'show_banner', 'show_web', 'show_mobile',
            'send_push', 'require_read', 'pin_home',
            'target_audience', 'target_plan', 'target_trainer', 'target_members',
        ]  # publish_at intentionally excluded — built from publish_date + publish_time
        widgets = {
            'title':          forms.TextInput(attrs={'class': 'form-control', 'maxlength': 150}),
            'description':    forms.Textarea(attrs={'class': 'form-control rich-text-editor', 'rows': 6}),
            'announcement_type': forms.Select(attrs={'class': 'form-select'}),
            'priority':       forms.Select(attrs={'class': 'form-select'}),
            'image':          forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'attachment':     forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'external_link':  forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://...'}),
            'expires_at':     forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'target_audience': forms.Select(attrs={'class': 'form-select', 'id': 'id_target_audience'}),
            'target_plan':    forms.Select(attrs={'class': 'form-select'}),
            'target_trainer': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, gym=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.gym = gym or (self.instance.gym_id and self.instance.gym)
        if self.gym:
            self.fields['target_plan'].queryset = MembershipPlan.objects.filter(gym=self.gym)
            self.fields['target_trainer'].queryset = Trainer.objects.filter(gym=self.gym)

            enrollments = list(
                Enrollment.objects
                .filter(gym=self.gym, is_deleted=False, user__isnull=False)
                .select_related('user')
            )
            user_ids = [e.user_id for e in enrollments]
            self.fields['target_members'].queryset = User.objects.filter(id__in=user_ids)
            self.fields['target_members']._enrollment_lookup = {e.user_id: e for e in enrollments}

        for name in ('target_plan', 'target_trainer', 'target_members'):
            self.fields[name].required = False

        if self.instance.pk and self.instance.publish_at:
            # Pre-fill from existing instance when editing.
            local_dt = timezone.localtime(self.instance.publish_at)
            self.fields['publish_date'].initial = local_dt.date()
            # Snap to nearest slot in case it was ever set outside the list.
            hhmm = local_dt.strftime('%H:%M')
            valid = {v for v, _ in PUBLISH_TIME_SLOTS}
            self.fields['publish_time'].initial = hhmm if hhmm in valid else PUBLISH_TIME_SLOTS[0][0]
        else:
            self.fields['publish_date'].initial = timezone.localdate()
            self.fields['publish_time'].initial = PUBLISH_TIME_SLOTS[0][0]

    def clean(self):
        cleaned = super().clean()
        publish_date = cleaned.get('publish_date')
        publish_time = cleaned.get('publish_time')
        expires_at = cleaned.get('expires_at')

        if publish_date and publish_time:
            hh, mm = map(int, publish_time.split(':'))
            naive = datetime.datetime.combine(publish_date, datetime.time(hh, mm))
            publish_at = timezone.make_aware(naive, timezone.get_current_timezone())
            cleaned['publish_at'] = publish_at

            if expires_at and expires_at <= publish_at:
                self.add_error('expires_at', "Expiry must be after the publish date.")

        audience = cleaned.get('target_audience')
        if audience == Announcement.Audience.PLAN and not cleaned.get('target_plan'):
            self.add_error('target_plan', "Select a plan for this audience type.")
        if audience == Announcement.Audience.TRAINER and not cleaned.get('target_trainer'):
            self.add_error('target_trainer', "Select a trainer for this audience type.")
        if audience == Announcement.Audience.SPECIFIC and not cleaned.get('target_members'):
            self.add_error('target_members', "Select at least one member.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.gym:
            instance.gym = self.gym
        instance.publish_at = self.cleaned_data['publish_at']
        if commit:
            instance.save()
            self.save_m2m()
        return instance