from django import forms
from django.core.validators import RegexValidator
from .models import DemoRequest

INDIAN_MOBILE_REGEX = RegexValidator(
    regex=r"^[6-9]\d{9}$",
    message="Enter a valid 10-digit Indian mobile number.",
)


class DemoRequestForm(forms.ModelForm):
    """Public-facing form for requesting a demo."""

    phone_number = forms.CharField(
        max_length=15,
        validators=[],  # validated manually after stripping spaces
        widget=forms.TextInput(attrs={
            "class": "form-control",
            "placeholder": "10-digit mobile number",
        }),
    )

    class Meta:
        model = DemoRequest
        fields = [
            "gym_name",
            "owner_name",
            "phone_number",
            "email",
            "city",
            "gym_size",
            "preferred_language",
            "message",
            "source",
        ]
        widgets = {
            "gym_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Gym Name"}),
            "owner_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Owner Name"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "Email (optional)"}),
            "city": forms.TextInput(attrs={"class": "form-control", "placeholder": "City (optional)"}),
            "gym_size": forms.Select(attrs={"class": "form-select"}),
            "preferred_language": forms.Select(attrs={"class": "form-select"}),
            "message": forms.Textarea(attrs={"class": "form-control", "rows": 4, "maxlength": 1000}),
            "source": forms.Select(attrs={"class": "form-select"}),
        }

    def clean_gym_name(self):
        value = self.cleaned_data["gym_name"].strip()
        if not value:
            raise forms.ValidationError("Gym name is required.")
        if len(value) > 150:
            raise forms.ValidationError("Gym name is too long (max 150 characters).")
        return value

    def clean_owner_name(self):
        value = self.cleaned_data["owner_name"].strip()
        if not value:
            raise forms.ValidationError("Owner name is required.")
        return value

    def clean_phone_number(self):
        value = self.cleaned_data["phone_number"].replace(" ", "").replace("-", "").strip()
        if value.startswith("+91"):
            value = value[3:]
        elif value.startswith("91") and len(value) == 12:
            value = value[2:]
        INDIAN_MOBILE_REGEX(value)
        return value

    def clean_email(self):
        value = self.cleaned_data.get("email")
        return value.strip() if value else value

    def clean_city(self):
        value = self.cleaned_data.get("city")
        return value.strip() if value else value

    def clean_message(self):
        value = self.cleaned_data.get("message")
        if value:
            value = value.strip()
            if len(value) > 1000:
                raise forms.ValidationError("Message cannot exceed 1000 characters.")
        return value