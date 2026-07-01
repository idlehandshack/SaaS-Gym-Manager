"""
Gym/branding.py
----------------
Resolves PWA branding for a gym using only real model fields:
logo, favicon, splash_logo, app_name, app_short_name, theme_color.

Apple touch icon and maskable icon are NOT separate uploads — both
are derived from `logo` at the size each platform expects.
"""
from django.templatetags.static import static
from cloudinary.utils import cloudinary_url
import logging

logger = logging.getLogger(__name__)

DEFAULT_THEME_COLOR = '#000000'
DEFAULT_BACKGROUND_COLOR = '#080808'   # no model field for this — fixed default
DEFAULT_APP_NAME = 'EnterGYM — Fitness Tracking & Attendance'
DEFAULT_APP_SHORT_NAME = 'EnterGYM'
DEFAULT_DESCRIPTION = 'EnterGYM — Auto GPS attendance, membership tracking, and workout logs.'


def _cloudinary_field_url(field, **transform):
    """Safely resolve a Cloudinary field to a URL. Returns None if empty/error."""
    if not field:
        return None
    try:
        public_id = field.public_id if hasattr(field, 'public_id') else str(field)
        if not public_id:
            return None
        url, _ = cloudinary_url(public_id, secure=True, fetch_format="auto",
                                 quality="auto", **transform)
        return url
    except Exception:
        logger.exception("Cloudinary URL resolution failed for field %s", field)
        return None


def get_gym_branding(gym):
    """
    Returns a dict of resolved branding values, falling back to project
    defaults for anything blank. Safe to call with gym=None.
    """
    defaults = {
        "app_name": DEFAULT_APP_NAME,
        "app_short_name": DEFAULT_APP_SHORT_NAME,
        "description": DEFAULT_DESCRIPTION,
        "logo_url": static('images/icon.webp'),
        "favicon_url": static('favicon.ico'),
        "splash_logo_url": static('images/splash.webp'),
        "apple_touch_icon_url": static('images/icon.webp'),
        "maskable_icon_url": static('images/icon.webp'),
        "shortcut_icon_url": static('images/icon.png'),
        "theme_color": DEFAULT_THEME_COLOR,
        "background_color": DEFAULT_BACKGROUND_COLOR,
    }

    if gym is None:
        return defaults

    app_name = gym.app_name or gym.gym_name or defaults["app_name"]
    app_short_name = gym.app_short_name or gym.gym_name or defaults["app_short_name"]

    logo_192 = _cloudinary_field_url(gym.logo, width=192, height=192, crop="fill") \
        or defaults["logo_url"]
    favicon_url = _cloudinary_field_url(gym.favicon, width=64, height=64, crop="fill") \
        or defaults["favicon_url"]
    splash_512 = _cloudinary_field_url(gym.splash_logo, width=512, height=512, crop="fill") \
        or defaults["splash_logo_url"]

    # Derived from logo — no separate uploads for these
    apple_touch_icon_url = _cloudinary_field_url(gym.logo, width=180, height=180, crop="fill") \
        or defaults["apple_touch_icon_url"]
    maskable_icon_url = _cloudinary_field_url(gym.logo, width=512, height=512, crop="fill") \
        or defaults["maskable_icon_url"]
    shortcut_icon_url = _cloudinary_field_url(gym.logo, width=96, height=96, crop="fill") \
        or defaults["shortcut_icon_url"]

    theme_color = gym.theme_color or defaults["theme_color"]

    return {
        "app_name": app_name,
        "app_short_name": app_short_name,
        "description": f"{app_name} — Auto GPS attendance, membership tracking, and workout logs.",
        "logo_url": logo_192,
        "favicon_url": favicon_url,
        "splash_logo_url": splash_512,
        "apple_touch_icon_url": apple_touch_icon_url,
        "maskable_icon_url": maskable_icon_url,
        "shortcut_icon_url": shortcut_icon_url,
        "theme_color": theme_color,
        "background_color": defaults["background_color"],  # fixed — no model field
    }