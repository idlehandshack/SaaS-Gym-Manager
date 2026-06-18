# notification/context_processors.py

from django.conf import settings


def vapid_key(request):
    """
    Makes VAPID_PUBLIC_KEY available in every Django template.

    Used by push_subscribe.js to register the browser for web push.
    VAPID keys are platform-global — one pair serves all gyms.

    Template usage:
        {% if VAPID_PUBLIC_KEY %}
            <div id="vapid-meta" data-key="{{ VAPID_PUBLIC_KEY }}"></div>
        {% endif %}
    """
    key = getattr(settings, "VAPID_PUBLIC_KEY", "")
    if not key:
        # Don't inject an empty string — templates check truthiness of this value
        return {}
    return {"VAPID_PUBLIC_KEY": key}