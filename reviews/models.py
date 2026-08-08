# reviews/models.py
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.cache import cache
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from Gym.models import Gym
from Gym.mixins import GymManager

RATING_VALIDATORS = [MinValueValidator(1), MaxValueValidator(5)]


class Review(models.Model):
    """
    One row per gym (OneToOne). This is intentional, not a limitation:
    the spec requires "only one active published review per gym" and lets
    the owner "update it later" — a single row that gets overwritten/
    republished is simpler and safer than deduping across many rows.
    `version` tracks how many times it has been (re)generated/approved.
    """

    gym = models.OneToOneField(
        Gym, on_delete=models.CASCADE, related_name='review'
    )
    # Denormalized on purpose: ownership of a gym can change hands, but the
    # review should still show who actually wrote it at the time.
    owner = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )

    objects = GymManager()

    # ── Ratings (all required, 1-5) ─────────────────────────────────────
    overall_rating = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    ease_of_use_rating = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    daily_work_rating = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    member_management_rating = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    attendance_rating = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    billing_rating = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    pending_payment_rating = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    analytics_rating = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    support_rating = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    value_rating = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)
    recommendation_rating = models.PositiveSmallIntegerField(validators=RATING_VALIDATORS)

    # ── AI + final content ──────────────────────────────────────────────
    ai_generated_review = models.TextField(blank=True)
    final_review = models.TextField(blank=True)
    overall_average = models.DecimalField(max_digits=3, decimal_places=2, default=0)

    is_published = models.BooleanField(default=False, db_index=True)
    is_hidden = models.BooleanField(
        default=False,
        help_text="Superadmin kill-switch: hides from public site without unpublishing/deleting."
    )

    version = models.PositiveIntegerField(default=0)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    RATING_FIELDS = [
        'overall_rating', 'ease_of_use_rating', 'daily_work_rating',
        'member_management_rating', 'attendance_rating', 'billing_rating',
        'pending_payment_rating', 'analytics_rating', 'support_rating',
        'value_rating', 'recommendation_rating',
    ]

    class Meta:
        ordering = ['-updated_at']
        indexes = [models.Index(fields=['is_published', 'is_hidden'])]
        verbose_name = 'Review'
        verbose_name_plural = 'Reviews'

    def compute_average(self):
        values = [getattr(self, f) for f in self.RATING_FIELDS]
        return round(sum(values) / len(values), 2)

    def save(self, *args, **kwargs):
        self.overall_average = self.compute_average()
        super().save(*args, **kwargs)

    @property
    def is_publicly_visible(self):
        return self.is_published and not self.is_hidden

    def __str__(self):
        status = 'Published' if self.is_published else 'Draft'
        return f"{self.gym.gym_name} review ({status}, v{self.version})"


class ReviewGenerationLog(models.Model):
    """
    Permanent audit trail of every AI generation call — required by the
    security spec ("Log every AI generation request") and doubles as the
    source of truth for the daily rate limit, so we don't rely on cache
    alone (cache can be flushed; this can't).
    """
    gym = models.ForeignKey(Gym, on_delete=models.CASCADE, db_index=True, related_name='review_generation_logs')
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    review = models.ForeignKey(Review, on_delete=models.SET_NULL, null=True, blank=True, related_name='generation_logs')

    ratings_snapshot = models.JSONField(default=dict)
    prompt_sent = models.TextField(blank=True)
    ai_response = models.TextField(blank=True)
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['gym', 'created_at'])]
        verbose_name = 'Review Generation Log'
        verbose_name_plural = 'Review Generation Logs'

    def delete(self, *args, **kwargs):
        raise PermissionError(
            "ReviewGenerationLog rows are a permanent audit trail and cannot be deleted."
        )

    def __str__(self):
        return f"{self.gym.gym_name} — {self.created_at:%d %b %Y %H:%M} ({'OK' if self.success else 'FAILED'})"


@receiver([post_save, post_delete], sender=Review)
def clear_review_cache(sender, instance, **kwargs):
    cache.delete(f"public_review_{instance.gym_id}")
