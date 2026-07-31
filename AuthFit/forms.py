# AuthFit/forms.py

import re
from Gym.models import EquipmentBrand,Service , Gym
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from AuthFit.models import Enrollment, MembershipPlan, Trainer ,LoginSupportQuery
from Gym.dashboard_stat_cards import STAT_CARD_REGISTRY, STAT_CARD_KEYS
from django.db import transaction
from django.utils import timezone
from Gym.theme import THEME_PRESETS
import logging
logger = logging.getLogger(__name__)
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
    gender = forms.ChoiceField(
        choices=[("", "Gender (Optional)")] + Enrollment.GENDER_CHOICES,
        required=False,
    )
    # NEW — lets the owner backdate old members' plan start.
    membership_start_date = forms.DateField(
        required=False,
        input_formats=['%Y-%m-%d'],
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'})
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

        # If money changed hands at the desk but no explicit payment_date was
        # entered, default it to the membership start date — this also gets
        # stamped onto the Payment record below.
        payment_date = self.cleaned_data.get('payment_date')
        if paid > 0 and not payment_date:
            payment_date = self.cleaned_data['membership_start_date']

        enrollment = Enrollment(
            gym=self.gym,
            fullname=self.cleaned_data['name'],
            phone=self.cleaned_data['phone'],
            gender=self.cleaned_data.get('gender') or None,
            selectPlan=plan,
            trainer=self.cleaned_data.get('trainer'),
            user=matched_user,
            source="MEMBER" if matched_user else "OWNER",
            profile_completed=False,
            paidAmount=paid,
            paymentMethod=self.cleaned_data.get('payment_method') or None,
            paymentDate=payment_date,
            paymentStatus="Done" if paid >= plan.price else "Pending",
            membership_start_date=self.cleaned_data['membership_start_date'],
        )
        if matched_user and matched_user.email:
            enrollment.email = matched_user.email

        enrollment.save()   # Amount / pendingAmount / DueDate / unique_id computed here

        # NEW — if anything was collected right now, record it as a real
        # billing.Payment (+ Invoice) immediately, the same as the Payment
        # Management flow. This is what makes "Last Payment" in the Member
        # Management Center accurate right away for Quick-Enrollment members,
        # instead of waiting for profile completion to backfill it.
        # Whether the invoice is *shown to the member* is a separate,
        # profile-completion-gated concern — handled in the Profile view.
        if paid > 0:
            from billing.models import Payment
            from billing.services.invoice_generator import create_invoice_for_payment
            from billing.services.pdf_generator import generate_invoice_pdf

            payment = Payment.objects.create(
                gym=self.gym,
                enrollment=enrollment,
                member_name=enrollment.fullname,
                member_phone=enrollment.phone,
                member_unique_id=enrollment.unique_id,
                plan_name=plan.plan,
                plan_duration_days=plan.duration_days,
                amount=enrollment.Amount,
                paid_amount=paid,
                pending_amount=enrollment.pendingAmount,
                payment_method=enrollment.paymentMethod,
                payment_date=payment_date,
                membership_start=enrollment.doj,
                membership_end=enrollment.DueDate,
            )
            invoice = create_invoice_for_payment(payment)
            try:
                generate_invoice_pdf(invoice)
            except Exception:
                logger.exception(
                    "PDF generation failed for invoice %s (quick enrollment)",
                    invoice.invoice_number,
                )
            enrollment.initial_invoice_generated = True
            enrollment.save(update_fields=["initial_invoice_generated"])

        return enrollment


class CompleteProfileForm(forms.ModelForm):
    """Member-facing — personal fields only. Req 6: nothing membership/financial-related exposed."""
    class Meta:
        model = Enrollment
        fields = ['email', 'address', 'reference']
 
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

    # ── theme fields ─────────────────────────────────────────────
    theme = forms.ChoiceField(choices=Gym.THEME_CHOICES, required=False)
    theme_color = forms.CharField(
        required=False,
        max_length=7,
        widget=forms.TextInput(attrs={'type': 'color'}),
    )
    dashboard_mode = forms.ChoiceField(choices=Gym.MODE_CHOICES, required=False)

    # ── NEW: contact fields ─────────────────────────────────────────
    contact_phone = forms.CharField(
        required=False,
        max_length=15,
        widget=forms.TextInput(attrs={'placeholder': '9876543210'}),
    )
    contact_email = forms.EmailField(
        required=False,
        max_length=254,
        widget=forms.EmailInput(attrs={'placeholder': 'yourgym@email.com'}),
    )
    plans = forms.ModelMultipleChoiceField(
        queryset=MembershipPlan.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    visible_stat_cards = forms.MultipleChoiceField(
        choices=STAT_CARD_REGISTRY,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    MAX_HOME_PLANS = 3
    _HANDLE_RE = re.compile(r'^[A-Za-z0-9._-]+$')
    _HEX_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')
    _PHONE_RE = re.compile(r'^[6-9]\d{9}$')

    def __init__(self, *args, gym=None, **kwargs):
        self.gym = gym
        initial = kwargs.pop('initial', {}) or {}
        super().__init__(*args, initial=initial, **kwargs)

        self.fields['services'].queryset = Service.objects.filter(is_active=True)
        self.fields['brands'].queryset = EquipmentBrand.objects.filter(is_active=True)
        self.fields['theme'] = forms.ChoiceField(choices=Gym.THEME_CHOICES, required=False)
        self.fields['plans'].queryset = (
            MembershipPlan.objects.filter(gym=gym).order_by('price') if gym
            else MembershipPlan.objects.none()
        )

        if gym is not None and not args:
            self.initial.setdefault('instagram_username', gym.instagram_username)
            self.initial.setdefault('services', gym.services.values_list('pk', flat=True))
            self.initial.setdefault('brands', gym.equipment_brands.values_list('pk', flat=True))
            all_keys = {k for k, _ in STAT_CARD_REGISTRY}
            hidden = set(gym.hidden_stat_cards or [])
            self.initial.setdefault('visible_stat_cards', list(all_keys - hidden))
            self.initial.setdefault('theme', gym.theme)
            self.initial.setdefault('theme_color', gym.theme_color)
            self.initial.setdefault('dashboard_mode', gym.dashboard_mode)
            self.initial.setdefault('contact_phone', gym.contact_phone)
            self.initial.setdefault('contact_email', gym.contact_email)
            self.initial.setdefault(
                'plans',
                MembershipPlan.objects.filter(gym=gym, show_on_home=True).values_list('pk', flat=True)
            )

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

    def clean_theme(self):
        value = self.cleaned_data.get('theme') or 'default'
        valid = {choice[0] for choice in Gym.THEME_CHOICES}
        return value if value in valid else 'default'

    def clean_dashboard_mode(self):
        value = self.cleaned_data.get('dashboard_mode') or 'dark'
        valid = {c[0] for c in Gym.MODE_CHOICES}
        return value if value in valid else 'dark'
    
    def clean_plans(self):
        plans = self.cleaned_data.get('plans')
        if plans and plans.count() > self.MAX_HOME_PLANS:
            raise forms.ValidationError(
                f"Select at most {self.MAX_HOME_PLANS} plans to show on your homepage."
            )
        return plans
    def clean_theme_color(self):
        value = (self.cleaned_data.get('theme_color') or '').strip()
        if value and not self._HEX_RE.match(value):
            raise forms.ValidationError("Enter a valid hex color, e.g. #ff5a00.")
        return value    
    def clean_contact_phone(self):
        value = (self.cleaned_data.get('contact_phone') or '').strip()
        if value and not self._PHONE_RE.match(value):
            raise forms.ValidationError("Enter a valid 10-digit Indian mobile number.")
        return value

    def clean(self):
        cleaned = super().clean()
        theme = cleaned.get('theme') or 'default'
        color = cleaned.get('theme_color')

        if theme == 'custom':
            if not color:
                self.add_error('theme_color', "Pick a color for a custom theme.")
        else:
            cleaned['theme_color'] = THEME_PRESETS.get(theme, '#ff5a00')

        return cleaned
    def clean_visible_stat_cards(self):
        # Guard against stale/unknown keys sneaking in from a tampered POST
        selected = self.cleaned_data.get('visible_stat_cards') or []
        return [k for k in selected if k in STAT_CARD_KEYS]
    def save(self):
        gym = self.gym
        all_keys = {k for k, _ in STAT_CARD_REGISTRY}
        visible = set(self.cleaned_data.get('visible_stat_cards') or [])
        gym.hidden_stat_cards = list(all_keys - visible)
        gym.services.set(self.cleaned_data['services'])
        gym.equipment_brands.set(self.cleaned_data['brands'])
        gym.instagram_username = self.cleaned_data['instagram_username']
        gym.theme = self.cleaned_data['theme']
        gym.theme_color = self.cleaned_data['theme_color']
        gym.dashboard_mode = self.cleaned_data['dashboard_mode']
        gym.contact_phone = self.cleaned_data['contact_phone']
        gym.contact_email = self.cleaned_data['contact_email']
        gym.save(update_fields=[
            'instagram_username', 'theme', 'theme_color', 'dashboard_mode',
            'contact_phone', 'contact_email','hidden_stat_cards'
        ])
        MembershipPlan.objects.filter(gym=gym).update(show_on_home=False)
        selected_ids = self.cleaned_data['plans'].values_list('pk', flat=True)
        MembershipPlan.objects.filter(gym=gym, pk__in=selected_ids).update(show_on_home=True)

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