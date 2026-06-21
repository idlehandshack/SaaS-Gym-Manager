# Gym/context_processors.py  (or wherever request.gym already gets attached)

import logging
from django.core.cache import cache
from cloudinary.utils import cloudinary_url

logger = logging.getLogger(__name__)


def gym_branding(request):
    gym = getattr(request, 'gym', None)
    if not gym:
        return {"gym_logo_url": None}

    cache_key = f"gym_logo_{gym.pk}"
    logo_url = cache.get(cache_key)

    if logo_url is None:
        logo_url = ""  # sentinel so "no logo" doesn't repeat the Cloudinary call every request
        if gym.logo:
            try:
                public_id = (
                    gym.logo.public_id
                    if hasattr(gym.logo, "public_id")
                    else str(gym.logo)
                )
                if public_id:
                    logo_url, _ = cloudinary_url(
                        public_id,
                        width=250, height=250,
                        crop="fit",
                        fetch_format="auto", quality="auto",
                        secure=True,
                    )
            except Exception:
                logger.exception("Cloudinary URL generation failed for gym logo (gym_id=%s)", gym.pk)
        cache.set(cache_key, logo_url, timeout=3600)

    return {"gym_logo_url": logo_url or None}