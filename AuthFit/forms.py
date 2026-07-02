# AuthFit/forms.py

import re
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from AuthFit.models import Enrollment, MembershipPlan, Trainer
from django.db import transaction


class UserLogin(UserCreationForm):
    """
    Signup form for gym members.
    Phone number is used as the Django username.

    Multi-tenancy:
        Same phone allowed across gyms (one Django User, multiple Enrollments).
        Duplicate blocked only if user is already enrolled at THIS gym.

    Usage:
        form = UserLogin(request.POST, gym=request.gym)
    """

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

    def __init__(self, *args, gym=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.gym = gym
        self.fields['plan'].queryset = MembershipPlan.objects.filter(gym=gym)
        self.fields['trainer'].queryset = Trainer.objects.filter(gym=gym)
        self._matched_user = None

    def clean(self):
        cleaned = super().clean()
        phone = cleaned.get('phone')
        if phone and self.gym:
            # Req 9 — is there already a Django User with this phone number?
            # (Assumes username == phone, matching loginPage()'s auth call.
            #  Swap this lookup if your User model stores phone elsewhere.)
            self._matched_user = User.objects.filter(username=phone).first()

            if self._matched_user:
                # Req 8 — block staff from creating a second enrollment
                # for someone already enrolled at this gym.
                if Enrollment.objects.filter(gym=self.gym, user=self._matched_user).exists():
                    raise forms.ValidationError(
                        "This phone number belongs to a member already enrolled at this gym."
                    )
            else:
                # Only pending (unlinked) rows need the uniqueness check here —
                # this mirrors the DB-level UniqueConstraint on (gym, phone) WHERE user IS NULL.
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
            user=matched_user,                              # Req 9: attach immediately if found
            source="MEMBER" if matched_user else "OWNER",
            profile_completed=False,
            paidAmount=paid,
            paymentMethod=self.cleaned_data.get('payment_method') or None,
            paymentDate=self.cleaned_data.get('payment_date'),
            paymentStatus="Done" if paid >= plan.price else "Pending",
        )
        if matched_user and matched_user.email:
            # Req 3 — backfill email from the existing account, never overwrite staff input
            enrollment.email = matched_user.email

        enrollment.save()   # Req 10: Amount / pendingAmount / DueDate / unique_id all computed in Enrollment.save() — untouched
        return enrollment


class CompleteProfileForm(forms.ModelForm):
    """Member-facing — personal fields only. Req 6: nothing membership/financial-related exposed."""
    class Meta:
        model = Enrollment
        fields = ['email', 'gender', 'address', 'reference', 'face_image']