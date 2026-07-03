# Gym/forms.py  (add to existing file, or create if UPI-only)
from django import forms
from .models import Gym


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