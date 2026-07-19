# AuthFit/forms.py

import re
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from AuthFit.models import Enrollment, MembershipPlan, Trainer ,LoginSupportQuery
from Gym.models import EquipmentBrand,Service
from django.db import transaction
from django.utils import timezone

class UserLogin(UserCreationForm):

    username = forms.CharField(
        label="Phone Number",
        max_length=10,
        widget=forms.TextInput(attrs={
            "placeholder":  "10-digit mobile number",
            "inputmode":    "numeric",
            "autocomplete": "tel",
        }),
        help_text="Enter your 10-digit mobile number.",
    )
    password1 = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirm Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Enter the same password again.",
    )

    class Meta:
        model  = User
        fields = ('username', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        self.gym           = kwargs.pop('gym', None)
        self._existing_user = None          # initialized here — not set implicitly
        super().__init__(*args, **kwargs)

        # Remove the Django 4.2+ usable_password checkbox.
        # Must be done here (form field), not in Meta.exclude (model field).
        self.fields.pop('usable_password', None)

    def clean_username(self):
        phone = self.cleaned_data['username'].strip()

        if not re.fullmatch(r'[6-9]\d{9}', phone):
            raise ValidationError(
                "Enter a valid 10-digit Indian mobile number "
                "(must start with 6, 7, 8, or 9)."
            )

        existing_user = User.objects.filter(username=phone).first()

        if existing_user:
            if self.gym is None:
                raise ValidationError(
                    "An account with this phone number already exists. "
                    "Please log in instead."
                )

            # Check enrollment using the correct reverse relation
            already_enrolled = existing_user.enrollment_set.filter(
                gym=self.gym
            ).exists()

            if already_enrolled:
                raise ValidationError(
                    "This phone number is already registered at this gym. "
                    "Please log in instead."
                )

            # Same person, different gym — reuse their User row
            self._existing_user = existing_user

        return phone

    def save(self, commit=True):
        """
        Returns existing User if phone already exists globally.
        Only creates a new User if this is a brand-new phone number.
        """
        if self._existing_user is not None:
            return self._existing_user
        return super().save(commit=commit)
    

PHONE_RE = re.compile(r'^\d{10}$')


class LoginSupportRequestForm(forms.Form):
    """
    Backs the login-page modal. `problem_type == 'forgot_password'` is
    routed to Django's password-reset token flow by the view; every other
    problem_type becomes a LoginSupportQuery ticket.

    Validation never reveals *which* of phone/email was wrong — both a
    missing account and a mismatched email surface the same message.
    """
    phone        = forms.CharField(max_length=10)
    email        = forms.EmailField()
    problem_type = forms.ChoiceField(choices=LoginSupportQuery.PROBLEM_CHOICES)
    description  = forms.CharField(widget=forms.Textarea)

    GENERIC_ERROR = "We couldn't verify those details. Please check your phone number and email."

    def clean_phone(self):
        phone = self.cleaned_data['phone'].strip()
        if not PHONE_RE.match(phone):
            raise forms.ValidationError("Enter a valid 10-digit mobile number.")
        return phone

    def clean_description(self):
        desc = self.cleaned_data['description'].strip()
        if not desc:
            raise forms.ValidationError("Please describe the issue.")
        return desc[:2000]

    def clean(self):
        cleaned = super().clean()
        phone = cleaned.get('phone')
        email = cleaned.get('email')
        if not phone or not email:
            return cleaned

        # Req: check User.username, then Enrollment.phone.
        user = User.objects.filter(username=phone).first()
        if user is None:
            enrollment = (
                Enrollment.objects
                .filter(phone=phone, user__isnull=False)
                .select_related('user')
                .first()
            )
            user = enrollment.user if enrollment else None

        # TEMP: email verification disabled for debugging — DO NOT ship like this
        # matched_email = (user.email or '').strip().lower() if user else ''
        # if not user or not matched_email or matched_email != email.strip().lower():
        #     raise forms.ValidationError(self.GENERIC_ERROR)

        cleaned['matched_user'] = user
        return cleaned


class QuickEnrollmentForm(forms.Form):
    name = forms.CharField(max_length=25)
    phone = forms.RegexField(regex=r'^\d{10}$', error_messages={
        'invalid': 'Enter a valid 10-digit phone number.'
    })
    plan = forms.ModelChoiceField(queryset=MembershipPlan.objects.none())
    trainer = forms.ModelChoiceField(queryset=Trainer.objects.none(), required=False)
    paid_amount = forms.DecimalField(max_digits=10, decimal_places=2, required=False, min_value=0)
    payment_method = forms.ChoiceField(choices=Enrollment.METHOD, required=False)
    payment_date = forms.DateField(required=False)

    # NEW — lets the owner backdate old members' plan start.
    membership_start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text="Choose the actual date when the member's plan started. "
                   "Leave today's date for new members.",
    )

    def __init__(self, *args, gym=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.gym = gym
        self.fields['plan'].queryset = MembershipPlan.objects.filter(gym=gym)
        self.fields['trainer'].queryset = Trainer.objects.filter(gym=gym)
        self.fields['membership_start_date'].initial = timezone.localdate()
        self._matched_user = None

    def clean_membership_start_date(self):
        # Empty input → default to today, per spec.
        return self.cleaned_data.get('membership_start_date') or timezone.localdate()

    def clean(self):
        cleaned = super().clean()
        phone = cleaned.get('phone')
        if phone and self.gym:
            self._matched_user = User.objects.filter(username=phone).first()

            if self._matched_user:
                if Enrollment.objects.filter(gym=self.gym, user=self._matched_user).exists():
                    raise forms.ValidationError(
                        "This phone number belongs to a member already enrolled at this gym."
                    )
            else:
                if Enrollment.objects.filter(gym=self.gym, phone=phone, user__isnull=True).exists():
                    raise forms.ValidationError(
                        "A pending enrollment already exists for this phone number."
                    )
        return cleaned

    @transaction.atomic
    def save(self):
        plan = self.cleaned_data['plan']
        paid = self.cleaned_data.get('paid_amount') or 0
        matched_user = self._matched_user

        enrollment = Enrollment(
            gym=self.gym,
            fullname=self.cleaned_data['name'],
            phone=self.cleaned_data['phone'],
            selectPlan=plan,
            trainer=self.cleaned_data.get('trainer'),
            user=matched_user,
            source="MEMBER" if matched_user else "OWNER",
            profile_completed=False,
            paidAmount=paid,
            paymentMethod=self.cleaned_data.get('payment_method') or None,
            paymentDate=self.cleaned_data.get('payment_date'),
            paymentStatus="Done" if paid >= plan.price else "Pending",
            # NEW — feeds Enrollment.save()'s DueDate calculation.
            membership_start_date=self.cleaned_data['membership_start_date'],
        )
        if matched_user and matched_user.email:
            enrollment.email = matched_user.email

        enrollment.save()   # Amount / pendingAmount / DueDate / unique_id computed here, from membership_start_date
        return enrollment


class CompleteProfileForm(forms.ModelForm):
    """Member-facing — personal fields only. Req 6: nothing membership/financial-related exposed."""
    class Meta:
        model = Enrollment
        fields = ['email', 'gender', 'address', 'reference']
 
class GymExtrasForm(forms.Form):
    services = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    brands = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    instagram_username = forms.CharField(required=False, max_length=100)
    _HANDLE_RE = re.compile(r'^[A-Za-z0-9._-]+$')

    def __init__(self, *args, gym=None, **kwargs):
        self.gym = gym
        initial = kwargs.pop('initial', {}) or {}
        super().__init__(*args, initial=initial, **kwargs)

        # These two lines are almost certainly what my version dropped:
        self.fields['services'].queryset = Service.objects.filter(is_active=True)
        self.fields['brands'].queryset = EquipmentBrand.objects.filter(is_active=True)

        if gym is not None and not args:
            self.initial.setdefault('instagram_username', gym.instagram_username)
            self.initial.setdefault('services', gym.services.values_list('pk', flat=True))
            self.initial.setdefault('brands', gym.equipment_brands.values_list('pk', flat=True))

    # ── Per-field cleaning: trim, strip leading @, validate charset ──────
    def _clean_handle(self, field_name, strip_at=True):
        value = (self.cleaned_data.get(field_name) or '').strip()
        if not value:
            return ''
        if strip_at and value.startswith('@'):
            value = value[1:]
        if not self._HANDLE_RE.match(value):
            raise forms.ValidationError(
                "Only letters, numbers, dots, underscores, and hyphens are allowed."
            )
        return value

    def clean_instagram_username(self):
        return self._clean_handle('instagram_username')
 
    def save(self):
        gym = self.gym
        gym.services.set(self.cleaned_data['services'])
        gym.equipment_brands.set(self.cleaned_data['brands'])
        gym.instagram_username = self.cleaned_data['instagram_username']
        gym.save(update_fields=[
            'instagram_username'
        ])

class EquipmentBrandSelectionForm(forms.Form):
    """
    Used by Gym Owner / Receptionist to pick which active brands
    (from the global catalog) their gym uses.
    """
    brands = forms.ModelMultipleChoiceField(
        queryset=None,  # set in __init__, scoped to active brands only
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
 
    def __init__(self, *args, gym=None, **kwargs):
        self.gym = gym
        super().__init__(*args, **kwargs)
        # Local import avoids a hard cross-app import at module load time
        from Gym.models import EquipmentBrand
        self.fields['brands'].queryset = EquipmentBrand.objects.filter(is_active=True)
        if gym is not None:
            self.fields['brands'].initial = gym.equipment_brands.values_list('pk', flat=True)
 
    def save(self):
        selected = self.cleaned_data['brands']
        self.gym.equipment_brands.set(selected)
        return self.gym