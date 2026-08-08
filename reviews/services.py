"""
reviews/services.py
--------------------
All business logic for the Verified AI Review System lives here, kept out
of views.py on purpose (same pattern as billing's service layer) so the
AI-call path can be unit tested without touching HTTP.
"""
import json
import logging
from datetime import timedelta
import random
from django.conf import settings
from django.utils import timezone

from AuthFit.models import Enrollment
from .models import Review, ReviewGenerationLog

logger = logging.getLogger('reviews')

MAX_GENERATIONS_PER_DAY = 3

# ──────────────────────────────────────────────────────────────────────────
# Feature checklist
# ──────────────────────────────────────────────────────────────────────────
# Always true for any active gym on the platform — never shown to the
# owner as a question, always included in "Modules Used".
BASELINE_FEATURES = [
    ("member_registration", "Member Registration (self-enroll + owner add)"),
    ("membership_expiry_tracking", "Membership Expiry Tracking"),
    ("pending_payment_tracking", "Pending Payment Tracking"),
    ("billing_invoice_management", "Billing & Invoice Management"),
    ("receipt_generation", "Receipt Generation"),
    ("manual_attendance", "Manual Attendance"),
    ("analytics", "Analytics & Reports"),
]

# Usage varies gym to gym — shown to the owner as a checklist so only what
# they actually use gets mentioned in the review.
OPTIONAL_FEATURE_CHOICES = [
    ("expense_management", "Expense Management"),
    ("revenue_dashboard", "Revenue Dashboard"),
    ("qr_attendance", "QR Code Attendance"),
    ("live_attendance_dashboard", "Live Attendance Dashboard"),
    ("face_attendance", "Face Recognition Attendance"),
    ("geo_attendance", "Geo-fenced Attendance"),
    ("ai_register_import", "AI Handwritten Register Attendance Import"),
    ("announcement_system", "Announcement System"),
    ("trainer_management", "Trainer Management"),
    ("store_ordering", "Store & Product Ordering"),
    ("public_website", "Public Gym Website"),
    ("mobile_app", "Mobile App"),
    ("data_security", "Data Security & Multi-Tenant Data Isolation"),
]

FEATURE_LABELS = dict(BASELINE_FEATURES + OPTIONAL_FEATURE_CHOICES)
BASELINE_LABELS = [label for _, label in BASELINE_FEATURES]

RATING_LABELS = {
    'overall_rating': 'Overall Satisfaction',
    'ease_of_use_rating': 'Ease of Use',
    'daily_work_rating': 'Reduced Daily Administrative Work',
    'member_management_rating': 'Member Management',
    'attendance_rating': 'Attendance Management',
    'billing_rating': 'Billing & Invoice Management',
    'pending_payment_rating': 'Pending Payment Tracking',
    'analytics_rating': 'Reports & Analytics',
    'support_rating': 'Technical Support',
    'value_rating': 'Value for Money',
    'recommendation_rating': 'Would Recommend EnterGYM',
}


class ReviewRateLimitExceeded(Exception):
    pass


class ReviewGenerationFailed(Exception):
    pass


# ──────────────────────────────────────────────────────────────────────────
# Auto-fetch (never ask the owner to type these)
# ──────────────────────────────────────────────────────────────────────────
def get_random_style():
    return random.choice([
        "Professional",
        "Friendly",
        "Minimal",
        "Storytelling",
        "Operational",
        "Conversational",
        "Matter-of-fact",
        "Enthusiastic but grounded",
    ])

def get_random_opening():
    """
    Forces structural variety so two gyms with similar ratings/features
    don't produce the same 'I've been using this software for about a
    month...' opening line every time.
    """
    return random.choice([
        "Open by mentioning a specific daily task that got easier.",
        "Open with how long you've used it and jump straight into the biggest benefit.",
        "Open by describing what running the gym was like before, briefly, then how it changed.",
        "Open with the single feature you rely on most, then broaden out.",
        "Open with a general impression first, save specifics for the middle.",
        "Open by mentioning your role or a typical day, then connect it to the software.",
    ])

def get_gym_snapshot(gym, selected_features=None):
    months_using = 1

    if gym.created_at:
        delta = timezone.now() - gym.created_at
        months_using = max(1, delta.days // 30)

    selected_features = selected_features or []
    optional_labels = [
        FEATURE_LABELS[key] for key in selected_features
        if key in dict(OPTIONAL_FEATURE_CHOICES)
    ]
    enabled_modules = BASELINE_LABELS + optional_labels

    member_count = Enrollment.objects.filter(gym=gym, is_active=True, is_deleted=False).count()

    return {
        'gym_name': gym.gym_name,
        'gym_logo': gym.logo.url if gym.logo else None,
        'owner_name': gym.owner.get_full_name() or gym.owner.username,
        'city': gym.city or '',
        'plan_name': gym.plan.name if gym.plan else 'Starter',
        'months_using': months_using,
        'enabled_modules': enabled_modules,
        'member_count': member_count,
        # No multi-branch model exists yet on Gym — omitted rather than guessed.
        'branch_count': None,
    }


# ──────────────────────────────────────────────────────────────────────────
# Rate limiting — DB log is the source of truth, cache is just a fast path
# ──────────────────────────────────────────────────────────────────────────
def check_rate_limit(gym):
    since = timezone.now() - timedelta(days=1)
    count = ReviewGenerationLog.objects.filter(gym=gym, created_at__gte=since).count()
    if count >= MAX_GENERATIONS_PER_DAY:
        raise ReviewRateLimitExceeded(
            f"Maximum {MAX_GENERATIONS_PER_DAY} review generations per day reached. Try again tomorrow."
        )
    return MAX_GENERATIONS_PER_DAY - count


# ──────────────────────────────────────────────────────────────────────────
# Prompt building
# ──────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are writing a verified customer review for a gym management software.

You will receive:
1. Gym Information
2. Modules Used
3. Star Ratings

CRITICAL RULES:
- The "Modules Used" list is the COMPLETE list of features available to this gym.
- NEVER mention any feature that does NOT appear in "Modules Used".
- Do NOT assume the gym has other modules.
- If a feature is missing from Modules Used, pretend it does not exist.
- Only mention data security or data protection if "Data Security & Multi-Tenant Data Isolation" appears in Modules Used — otherwise never bring up security, privacy, or data protection at all.

Additional rules:
- Write like a real gym owner.
- Never advertise.
- Never exaggerate.
- Never invent statistics or business growth.
- Never invent member counts.
- Only discuss features present in Modules Used.
- Only discuss categories rated 4 or 5 in detail.
- If a category is rated below 4, mention it briefly and fairly.
- Write 120-170 words.
- Use first-person language.
- Never mention AI or that the review was generated.
- Never use emojis.
"""


def build_user_prompt(gym_info, ratings):
    style = get_random_style()
    opening = get_random_opening()
    ratings_readable = {
        RATING_LABELS[field]: value for field, value in ratings.items()
    }
    payload = {
        "Gym Information": {
        "Gym Name": gym_info["gym_name"],
        "City": gym_info["city"],
        "Months Using": gym_info["months_using"],
        "Plan": gym_info["plan_name"],
        "Active Members": gym_info["member_count"],
        },
        'Modules Used': gym_info['enabled_modules'],
        'Ratings (out of 5)': ratings_readable,
    }
    return f"""
        Gym Information, Modules Used, and Ratings are below.

        {json.dumps(payload, indent=2, ensure_ascii=False)}

        Generate ONE review.
        Writing Style: {style}
        Structural instruction: {opening}

        Requirements:

        - Sound like a genuine gym owner.
        - Never sound like marketing.
        - Use natural English.
        - Vary sentence structure — do not default to a generic template opening.
        - Do not start with "I've been using this software for about a month" or any near-identical phrasing unless the Structural instruction specifically calls for it.
        - Write 130–170 words.
        - Never invent features.
        - Never invent statistics.
        - Only discuss supplied modules.
        - Mention benefits implied by high ratings.
        - Never mention AI.
        """
def call_ai_model(system_prompt, user_prompt):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ReviewGenerationFailed("openai package not installed on server.") from exc

    api_key = getattr(settings, 'OPENAI_API_KEY', None)
    if not api_key:
        raise ReviewGenerationFailed("OPENAI_API_KEY is not configured.")

    client = OpenAI(api_key=api_key)
    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_output_tokens=250,
            temperature=1.1,   # higher variance so similar inputs don't collapse to the same phrasing
            top_p=0.95,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("GPT-5 mini call failed for review generation")
        raise ReviewGenerationFailed(
           "AI review generation is temporarily unavailable because the API quota has been reached. Please contact the administrator."
        ) from exc
    text = getattr(response, "output_text", "")
    text = text.strip()
    if not text:
        raise ReviewGenerationFailed("The AI returned an empty response. Please try again.")
    words = len(text.split())

    if words < 100:
        raise ReviewGenerationFailed(
            "Generated review was unexpectedly short."
        )

    if words > 220:
        text = " ".join(text.split()[:220])

    blocked = [
        "ChatGPT",
        "OpenAI",
        "GPT",
        "AI generated",
    ]

    if any(word.lower() in text.lower() for word in blocked):
        raise ReviewGenerationFailed(
            "Generated review failed validation."
        )
    return text


# ──────────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────────
def generate_review_draft(gym, owner, ratings, selected_features=None):
    """
    ratings: dict of {field_name: int(1-5)} for all Review.RATING_FIELDS.
    selected_features: list of keys from OPTIONAL_FEATURE_CHOICES the owner
        checked on the form. Baseline features are added automatically.
    Returns the Review instance with a fresh ai_generated_review draft.
    Raises ReviewRateLimitExceeded / ReviewGenerationFailed.
    """
    remaining_before = check_rate_limit(gym)  # raises if exceeded

    gym_info = get_gym_snapshot(gym, selected_features=selected_features)
    user_prompt = build_user_prompt(gym_info, ratings)

    review, _ = Review.objects.get_or_create(gym=gym, defaults={**ratings, 'owner': owner})
    for field, value in ratings.items():
        setattr(review, field, value)
    review.owner = owner

    log = ReviewGenerationLog(
        gym=gym, owner=owner, review=review,
        ratings_snapshot={'ratings': ratings, 'features': selected_features or []},
        prompt_sent=user_prompt,
    )

    try:
        ai_text = call_ai_model(SYSTEM_PROMPT, user_prompt)
    except ReviewGenerationFailed as exc:
        log.success = False
        log.error_message = str(exc)
        log.save()
        raise

    review.ai_generated_review = ai_text
    review.final_review = review.final_review or ai_text
    review.version += 1
    review.is_published = False  # any regeneration reverts to draft until re-approved
    review.save()

    log.ai_response = ai_text
    log.save()

    logger.info("Review generated for gym=%s version=%s", gym.gym_code, review.version)
    return review, remaining_before - 1


def approve_review(review, final_text):
    final_text = (final_text or "").strip()
    if not final_text:
        raise ValueError("Final review text cannot be empty.")
    review.final_review = final_text
    review.is_published = True
    review.approved_at = timezone.now()
    review.save()
    return review