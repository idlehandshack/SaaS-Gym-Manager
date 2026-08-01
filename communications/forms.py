from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone
import datetime

from AuthFit.models import MembershipPlan
from Gym.models import Gym, SubscriptionPlan

from .models import (
    Communication, CommunicationAudience, CommunicationCampaign, CommunicationSponsor,
)
from .utils import sanitize_rich_text

# ── Upload validation (unchanged) ──
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024
MAX_ATTACHMENT_SIZE_BYTES = 15 * 1024 * 1024
ALLOWED_IMAGE_CONTENT_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
ALLOWED_ATTACHMENT_CONTENT_TYPES = {
    'application/pdf', 'image/jpeg', 'image/png',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
}

# Keep in sync with the cron schedule in publish_scheduled_communications.
PUBLISH_TIME_SLOTS = [
    ('07:00', '7:00 AM'),
    ('13:00', '1:00 PM'),
    ('19:00', '7:00 PM'),
]


def _validate_upload(f, *, max_bytes, allowed_types, label):
    if f is None:
        return
    content_type = getattr(f, 'content_type', None)
    if content_type and content_type not in allowed_types:
        raise ValidationError(f"{label} must be one of: {', '.join(sorted(allowed_types))}.")
    size = getattr(f, 'size', None)
    if size and size > max_bytes:
        raise ValidationError(f"{label} must be under {max_bytes // (1024 * 1024)} MB.")


class CommunicationForm(forms.ModelForm):

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
        model = Communication
        fields = [
            'title', 'description', 'type', 'priority',
            'image', 'attachment', 'external_link', 'deep_link_type',
            'expires_at', 'is_active',
            'channel_push', 'channel_web_push', 'channel_pwa',
            'channel_email', 'channel_whatsapp', 'channel_sms',
            'show_popup', 'require_read', 'show_banner', 'banner_placement',
            'show_notification_center',
            'campaign',
        ]  # publish_at intentionally excluded — built from publish_date + publish_time
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'maxlength': 150}),
            'description': forms.Textarea(attrs={'class': 'form-control rich-text-editor', 'rows': 6}),
            'type': forms.Select(attrs={'class': 'form-select'}),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'image': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'attachment': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'external_link': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://...'}),
            'deep_link_type': forms.Select(attrs={'class': 'form-select'}),
            'expires_at': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'banner_placement': forms.Select(attrs={'class': 'form-select'}),
            'campaign': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['campaign'].queryset = CommunicationCampaign.objects.all()
        self.fields['campaign'].required = False

        if self.instance.pk and self.instance.publish_at:
            local_dt = timezone.localtime(self.instance.publish_at)
            self.fields['publish_date'].initial = local_dt.date()
            hhmm = local_dt.strftime('%H:%M')
            valid = {v for v, _ in PUBLISH_TIME_SLOTS}
            self.fields['publish_time'].initial = hhmm if hhmm in valid else PUBLISH_TIME_SLOTS[0][0]
        else:
            self.fields['publish_date'].initial = timezone.localdate()
            self.fields['publish_time'].initial = PUBLISH_TIME_SLOTS[0][0]

    def clean_description(self):
        return sanitize_rich_text(self.cleaned_data.get('description', ''))

    def clean_image(self):
        f = self.cleaned_data.get('image')
        _validate_upload(f, max_bytes=MAX_IMAGE_SIZE_BYTES, allowed_types=ALLOWED_IMAGE_CONTENT_TYPES, label="Image")
        return f

    def clean_attachment(self):
        f = self.cleaned_data.get('attachment')
        _validate_upload(
            f, max_bytes=MAX_ATTACHMENT_SIZE_BYTES, allowed_types=ALLOWED_ATTACHMENT_CONTENT_TYPES,
            label="Attachment",
        )
        return f

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

        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.publish_at = self.cleaned_data['publish_at']
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    
class CommaSeparatedField(forms.CharField):
    """Renders/parses a JSONField-backed list (cities/states/countries) as
    a plain comma-separated text input."""

    def prepare_value(self, value):
        if isinstance(value, list):
            return ', '.join(value)
        return value or ''

    def to_python(self, value):
        if not value:
            return []
        return [part.strip() for part in value.split(',') if part.strip()]


class CommunicationAudienceForm(forms.ModelForm):

    cities = CommaSeparatedField(
        required=False, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Mumbai, Pune'}),
        help_text="Comma-separated city names, matched against Gym.city.",
    )
    states = CommaSeparatedField(
        required=False, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Not supported yet'}),
        help_text="Not resolvable yet — Gym has no state field. Stored for when it is added.",
    )
    countries = CommaSeparatedField(
        required=False, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Not supported yet'}),
        help_text="Not resolvable yet — Gym has no country field. Stored for when it is added.",
    )

    class Meta:
        model = CommunicationAudience
        fields = [
            'scope', 'gyms', 'plan_name_filter', 'plans', 'subscription_plans',
            'cities', 'states', 'countries', 'specific_members', 'specific_staff',
        ]
        widgets = {
            'scope': forms.Select(attrs={'class': 'form-select', 'id': 'id_scope'}),
            'gyms': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 8}),
            'plan_name_filter': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Gold'}),
            'plans': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 8}),
            'subscription_plans': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 6}),
            'specific_members': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 8}),
            'specific_staff': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 8}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['gyms'].queryset = Gym.objects.filter(active=True).order_by('gym_name')
        self.fields['plans'].queryset = MembershipPlan.objects.select_related('gym').order_by('gym__gym_name', 'plan')
        self.fields['subscription_plans'].queryset = SubscriptionPlan.objects.all()
        self.fields['specific_members'].queryset = User.objects.filter(is_active=True).order_by('username')
        self.fields['specific_staff'].queryset = User.objects.filter(is_active=True, is_staff=True).order_by('username')
        for name in ('gyms', 'plan_name_filter', 'plans', 'subscription_plans',
                     'cities', 'states', 'countries', 'specific_members', 'specific_staff'):
            self.fields[name].required = False

    def clean(self):
        cleaned = super().clean()
        scope = cleaned.get('scope')

        requires = {
            CommunicationAudience.Scope.SPECIFIC_PLAN: (
                'plans', 'plan_name_filter', "Pick at least one plan or enter a plan name to match."
            ),
            CommunicationAudience.Scope.SUBSCRIPTION_PLAN: (
                'subscription_plans', None, "Select at least one SaaS subscription plan."
            ),
            CommunicationAudience.Scope.SPECIFIC_CITY: ('cities', None, "Enter at least one city."),
            CommunicationAudience.Scope.SPECIFIC_MEMBERS: ('specific_members', None, "Select at least one member."),
            CommunicationAudience.Scope.SPECIFIC_STAFF: ('specific_staff', None, "Select at least one staff member."),
        }

        if scope in requires:
            field_name, alt_field, message = requires[scope]
            primary_empty = not cleaned.get(field_name)
            alt_empty = alt_field is None or not cleaned.get(alt_field)
            if primary_empty and alt_empty:
                self.add_error(field_name, message)

        if scope in (CommunicationAudience.Scope.SPECIFIC_STATE, CommunicationAudience.Scope.SPECIFIC_COUNTRY):
            self.add_error(
                None,
                "State/Country targeting isn't resolvable yet — Gym has no state/country field. "
                "Use Specific City or Specific Gym(s) instead for now.",
            )

        return cleaned


class CommunicationSponsorForm(forms.ModelForm):
    class Meta:
        model = CommunicationSponsor
        fields = ['name', 'logo', 'website', 'contact_person', 'email', 'phone', 'status']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'logo': forms.ClearableFileInput(attrs={'class': 'form-control'}),
            'website': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://...'}),
            'contact_person': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
        }


class CommunicationCampaignForm(forms.ModelForm):
    class Meta:
        model = CommunicationCampaign
        fields = ['name', 'sponsor', 'budget', 'start_date', 'end_date', 'status']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'sponsor': forms.Select(attrs={'class': 'form-select'}),
            'budget': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'start_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'end_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['sponsor'].queryset = CommunicationSponsor.objects.all()
        self.fields['sponsor'].required = False