# reviews/views.py
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .models import Review
from .services import (
    RATING_LABELS, generate_review_draft, approve_review,
    ReviewRateLimitExceeded, ReviewGenerationFailed, get_gym_snapshot,OPTIONAL_FEATURE_CHOICES,
)

RATING_FIELDS = list(RATING_LABELS.keys())


def gym_owner_required(view_func):
    """
    Mirrors the gym-scoped auth pattern used across AuthFit/billing:
    - must be authenticated
    - must have a resolved tenant (request.gym)
    - must be that gym's owner specifically (not trainer/receptionist/member/superadmin)
    """
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if request.is_super_admin:
            return JsonResponse({'error': 'Superadmins cannot submit gym reviews.'}, status=403)
        if not request.gym:
            return JsonResponse({'error': 'No gym context found.'}, status=400)
        if request.gym.owner_id != request.user.id:
            return JsonResponse({'error': 'Only the gym owner can manage the review.'}, status=403)
        return view_func(request, *args, **kwargs)
    return _wrapped


def _ratings_from_post(data):
    ratings = {}
    errors = {}
    for field in RATING_FIELDS:
        raw = data.get(field)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            errors[field] = "Required, must be a whole number 1-5."
            continue
        if value < 1 or value > 5:
            errors[field] = "Must be between 1 and 5."
            continue
        ratings[field] = value
    return ratings, errors


@gym_owner_required
def review_page(request):
    """
    GET  -> renders the review builder (star selectors + any existing draft/final)
            or the thank-you screen if a review is already published.
    """
    gym = request.gym
    review = Review.objects.filter(gym=gym).first()
    gym_info = get_gym_snapshot(gym)

    context = {
        'review': review,
        'gym_info': gym_info,
        'rating_fields': [
            {'name': f, 'label': RATING_LABELS[f]} for f in RATING_FIELDS
        ],
        'feature_choices': OPTIONAL_FEATURE_CHOICES,
        'already_published': bool(review and review.is_published),
    }
    return render(request, 'reviews/owner_review.html', context)


@gym_owner_required
@require_POST
def generate_review(request):
    """
    AJAX endpoint: validates ratings, enforces rate limit, calls GPT-5 mini,
    stores + returns the draft. Owner reviews it client-side before approving.
    """
    existing = Review.objects.filter(gym=request.gym, is_published=True).first()
    if existing:
        return JsonResponse({'error': 'A review has already been published for this gym.'}, status=403)
    ratings, errors = _ratings_from_post(request.POST)
    if errors:
        return JsonResponse({'error': 'Invalid ratings.', 'field_errors': errors}, status=400)

    try:
        review, remaining_today = generate_review_draft(request.gym, request.user, ratings)
    except ReviewRateLimitExceeded as exc:
        return JsonResponse({'error': str(exc)}, status=429)
    except ReviewGenerationFailed as exc:
        return JsonResponse({'error': str(exc), 'retry': True}, status=502)

    return JsonResponse({
        'draft': review.ai_generated_review,
        'version': review.version,
        'remaining_generations_today': remaining_today,
    })


@gym_owner_required
@require_POST
def approve_review_view(request):
    """
    AJAX endpoint: owner edits (optional) then approves -> publishes.
    """
    review = Review.objects.filter(gym=request.gym).first()
    if not review or not review.ai_generated_review:
        return JsonResponse({'error': 'Generate a review draft first.'}, status=400)

    final_text = request.POST.get('final_review', '').strip()
    if not (120 <= len(final_text.split()) or final_text):
        pass  # word-count is a soft UX nudge in the template, not a hard server rule

    try:
        approve_review(review, final_text)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    return JsonResponse({
        'success': True,
        'final_review': review.final_review,
        'approved_at': review.approved_at.isoformat(),
    })
