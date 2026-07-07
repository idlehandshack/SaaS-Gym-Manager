import re
from django.db import models
from django.core.validators import RegexValidator


class DemoRequest(models.Model):
    """A prospective gym owner's request for a product demo."""

    class GymSize(models.TextChoices):
        TINY = "lt_100", "Less than 100 members"
        SMALL = "100_300", "100–300 members"
        MEDIUM = "300_700", "300–700 members"
        LARGE = "gt_700", "More than 700 members"

    class Language(models.TextChoices):
        ENGLISH = "en", "English"
        HINDI = "hi", "Hindi"
        HINGLISH = "hinglish", "Hinglish"

    class Source(models.TextChoices):
        WEBSITE = "website", "Website"
        INSTAGRAM = "instagram", "Instagram"
        FACEBOOK = "facebook", "Facebook"
        GOOGLE = "google", "Google"
        DIRECT = "direct", "Direct"
        OTHER = "other", "Other"

    phone_validator = RegexValidator(
        regex=r"^[6-9]\d{9}$",
        message="Enter a valid 10-digit Indian mobile number.",
    )

    # Core fields
    gym_name = models.CharField(max_length=150)
    owner_name = models.CharField(max_length=100)
    phone_number = models.CharField(max_length=10, validators=[phone_validator], db_index=True)
    email = models.EmailField(blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)

    gym_size = models.CharField(max_length=20, choices=GymSize.choices)
    preferred_language = models.CharField(max_length=20, choices=Language.choices, default=Language.ENGLISH)
    message = models.TextField(max_length=1000, blank=True, null=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.WEBSITE)

    # Auto-captured metadata
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=500, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Internal / CRM fields
    contacted = models.BooleanField(default=False)
    contacted_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    # Extension points for future notification wiring
    email_sent = models.BooleanField(default=False)
    push_sent = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Demo Request"
        verbose_name_plural = "Demo Requests"
        indexes = [
            models.Index(fields=["phone_number", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.gym_name} — {self.owner_name} ({self.phone_number})"