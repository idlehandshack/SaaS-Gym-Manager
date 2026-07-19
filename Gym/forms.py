# Gym/forms.py
from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from .models import Gym, SubscriptionPlan, StaffProfile, GymGSTProfile
from .models import StaffPermission, PERMISSION_DEFINITIONS

class StaffPermissionForm(forms.ModelForm):
    class Meta:
        model = StaffPermission
        fields = [f for f, _, _ in PERMISSION_DEFINITIONS]
        widgets = {f: forms.CheckboxInput() for f, _, _ in PERMISSION_DEFINITIONS}

class PlanChoiceField(forms.ModelChoiceField):
    """Shows price + limits directly in the dropdown option text."""
    def label_from_instance(self, plan):
        return f"{plan.name} — ₹{plan.price_monthly:.0f}/mo · {plan.member_limit} members · {plan.trainer_limit} trainers"
class UPISettingsForm(forms.ModelForm):
    class Meta:
        model = Gym
        fields = ['upi_enabled', 'upi_id', 'upi_display_name', 'upi_payment_note']

    def clean(self):
        cleaned = super().clean()
        enabled = cleaned.get('upi_enabled')
        upi_id = (cleaned.get('upi_id') or '').strip()
        display_name = (cleaned.get('upi_display_name') or '').strip()
        if enabled:
            if not upi_id:
                self.add_error('upi_id', "UPI ID cannot be empty when UPI is enabled.")
            if not display_name:
                self.add_error('upi_display_name', "Display Name cannot be empty when UPI is enabled.")
        return cleaned


class GymCreateForm(forms.ModelForm):
    """
    Full-field gym creation form. Owner is an EXISTING User — looked up by
    phone number (which is stored as username). No credentials are created here.
    """

    owner_phone = forms.CharField(
        max_length=15,
        label="Owner Phone Number",
        help_text="Must match an existing user's username exactly.",
    )
    plan = PlanChoiceField(
        queryset=SubscriptionPlan.objects.all(),
        required=False,
        empty_label="— No plan —",
    )

    class Meta:
        model = Gym
        fields = [
            "gym_name", "gym_code", "plan", "active",
            "subscription_start", "subscription_end",
            "member_limit", "trainer_limit",
            "enable_store", "enable_attendance",
            "enable_face_recognition", "enable_trainers",
            "app_name", "app_short_name",
            "logo", "favicon", "splash_logo",
            "theme_color", "receipt_footer",
            "contact_email", "contact_phone", "whatsapp_number",
            "address", "city", "app_download_url",
            "upi_enabled", "upi_id", "upi_display_name", "upi_payment_note",
            "latitude", "longitude", "radius_meters", "map",
        ]
        widgets = {
            "subscription_start": forms.DateInput(attrs={"type": "date"}),
            "subscription_end":   forms.DateInput(attrs={"type": "date"}),
            "theme_color":        forms.TextInput(attrs={"type": "color"}),
            "latitude":           forms.NumberInput(attrs={"step": "any"}),
            "longitude":          forms.NumberInput(attrs={"step": "any"}),
            "radius_meters":      forms.NumberInput(attrs={"step": "any"}),
            "receipt_footer":     forms.Textarea(attrs={"rows": 2}),
            "map":                forms.Textarea(attrs={"rows": 2}),
            "address":            forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        optional_fields = [
            "subscription_start", "subscription_end", "city", "address", "app_name", "app_short_name", "whatsapp_number",
            "app_download_url", "latitude", "longitude", "radius_meters", "map",
            "logo", "favicon", "splash_logo", "receipt_footer",
            "contact_email", "contact_phone",
        ]
        for f in optional_fields:
            self.fields[f].required = False

    def clean_owner_phone(self):
        phone = self.cleaned_data["owner_phone"].strip()
        try:
            user = User.objects.get(username=phone)
        except User.DoesNotExist:
            raise ValidationError("No user found with this phone number. Ask them to sign up first.")
        if hasattr(user, "owned_gym"):
            raise ValidationError(f"This user already owns a gym ({user.owned_gym.gym_name}).")
        self.cleaned_data["owner_user"] = user
        return phone

    def clean_gym_code(self):
        code = self.cleaned_data["gym_code"]
        if Gym.objects.filter(gym_code=code).exists():
            raise ValidationError("That gym code is already in use.")
        return code

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("upi_enabled"):
            if not (cleaned.get("upi_id") or "").strip():
                self.add_error("upi_id", "UPI ID is required when UPI is enabled.")
            if not (cleaned.get("upi_display_name") or "").strip():
                self.add_error("upi_display_name", "Display Name is required when UPI is enabled.")
        return cleaned

    def save(self, commit=True):
        gym = super().save(commit=False)
        gym.owner = self.cleaned_data["owner_user"]
        if commit:
            gym.save()
            StaffProfile.objects.get_or_create(
                user=gym.owner, defaults={"gym": gym, "role": "gym_owner", "active": True}
            )
        return gym


class StaffProfileCreateForm(forms.Form):
    """Adds an EXISTING user as staff (looked up by phone/username), same pattern as owner."""
    phone  = forms.CharField(max_length=15, label="Staff Phone Number")
    role   = forms.ChoiceField(choices=StaffProfile.ROLE_CHOICES)
    active = forms.BooleanField(required=False, initial=True)

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip()
        try:
            user = User.objects.get(username=phone)
        except User.DoesNotExist:
            raise ValidationError("No user found with this phone number.")
        if hasattr(user, "staff_profile"):
            raise ValidationError(f"This user already has a staff profile ({user.staff_profile.gym}).")
        self.cleaned_data["staff_user"] = user
        return phone

    def save(self, gym):
        user = self.cleaned_data["staff_user"]
        return StaffProfile.objects.create(
            user=user,
            gym=gym,
            role=self.cleaned_data["role"],
            active=self.cleaned_data.get("active", True),
        )


class GymGSTProfileForm(forms.ModelForm):
    class Meta:
        model = GymGSTProfile
        exclude = ["gym"]
        widgets = {"signature_image": forms.URLInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in ["address_line2", "gstin", "signature_image"]:
            self.fields[f].required = False


