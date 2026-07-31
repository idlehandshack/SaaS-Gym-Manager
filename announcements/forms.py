from django import forms
from django.contrib.auth.models import User
from django.utils import timezone

from AuthFit.models import Enrollment, MembershipPlan, Trainer

from .models import Announcement


class MemberModelMultipleChoiceField(forms.ModelMultipleChoiceField):
    """Shows 'Fullname (phone)' instead of the raw username in the widget."""

    def __init__(self, *args, enrollment_lookup=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._enrollment_lookup = enrollment_lookup or {}

    def label_from_instance(self, user):
        enr = self._enrollment_lookup.get(user.pk)
        if enr:
            return f"{enr.fullname} ({enr.phone})"
        return user.get_full_name() or user.username


class AnnouncementForm(forms.ModelForm):
    """
    Bootstrap-5-friendly ModelForm. `gym` is injected by the view (never
    user-editable) and target_plan/target_trainer/target_members querysets
    are scoped to the current gym in __init__.
    """

    target_members = MemberModelMultipleChoiceField(
        queryset=User.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={'class': 'form-select', 'size': 8}),
    )

    class Meta:
        model = Announcement
        fields = [
            'title', 'description', 'announcement_type', 'priority',
            'image', 'attachment', 'external_link',
            'publish_at', 'expires_at', 'is_active',
            'show_popup', 'show_banner', 'show_web', 'show_mobile',
            'send_push', 'require_read', 'pin_home',
            'target_audience', 'target_plan', 'target_trainer', 'target_members',
        ]
        widgets = {
            'title':          forms.TextInput(attrs={'class': 'form-control', 'maxlength': 150}),
            'description':    forms.Textarea(attrs={'class': 'form-control rich-text-editor', 'rows': 6}),
            'announcement_type': forms.Select(attrs={'class': 'form-select'}),
            'priority':       forms.Select(attrs={'class': 'form-select'}),
            'image':          forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'attachment':     forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'external_link':  forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://...'}),
            'publish_at':     forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
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

            # target_members must be a User queryset (the model field is
            # ManyToManyField(User)) — resolve gym members via Enrollment,
            # not Enrollment rows themselves.
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

        if not self.instance.pk:
            self.fields['publish_at'].initial = timezone.now()

    def clean(self):
        cleaned = super().clean()
        publish_at = cleaned.get('publish_at')
        expires_at = cleaned.get('expires_at')
        if publish_at and expires_at and expires_at <= publish_at:
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
        if commit:
            instance.save()
            self.save_m2m()
        return instance