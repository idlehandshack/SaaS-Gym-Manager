# AuthFit/forms.py

import re
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError


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