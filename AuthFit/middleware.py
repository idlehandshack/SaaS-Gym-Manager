# AuthFit/middleware.py
import secrets


class SecurityHeadersMiddleware:
    """
    Adds security headers to every HTTP response.

    CSP nonce is generated per-request and attached to request.csp_nonce
    so templates can use it:
        <script nonce="{{ request.csp_nonce }}">...</script>
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):

        # Generate a fresh nonce per request — used in CSP + templates
        nonce = secrets.token_urlsafe(16)
        request.csp_nonce = nonce

        response = self.get_response(request)

        # ── Standard security headers ─────────────────────────────────────
        response["X-Content-Type-Options"] = "nosniff"
        response["X-Frame-Options"]        = "DENY"
        response["Referrer-Policy"]        = "strict-origin-when-cross-origin"
        response["Cross-Origin-Opener-Policy"] = "same-origin"

        response["Permissions-Policy"] = (
            "geolocation=(self), "
            "camera=(), "
            "microphone=(), "
            "payment=()"
        )

        # ── Content Security Policy ───────────────────────────────────────
        #
        # script-src:
        #   'self'                        → your own JS files
        #   'nonce-{nonce}'               → inline scripts with the nonce attr
        #   https://cdn.jsdelivr.net      → any CDN scripts (chart.js etc)
        #   https://www.gstatic.com       → Firebase SDK
        #   https://www.googleapis.com    → Firebase auth/messaging
        #
        # connect-src:
        #   'self'                        → your own API endpoints
        #   https://fcm.googleapis.com    → FCM push registration
        #   https://firebaseinstallations.googleapis.com → Firebase installs
        #   https://api.cloudinary.com    → Cloudinary uploads from browser
        #   https://*.cloudinary.com      → Cloudinary image delivery
        #
        # img-src:
        #   'self' data: blob:            → local + inline images
        #   https://res.cloudinary.com    → Cloudinary images
        #   https://*.cloudinary.com      → Cloudinary subdomains
        #
        # worker-src:
        #   'self'                        → service worker (sw.js for geo/PWA)
        #   blob:                         → Firebase messaging service worker
        #
        # frame-src:
        #   https://www.google.com        → Google Maps embeds if used

        response["Content-Security-Policy"] = (
            f"default-src 'self'; "

            f"script-src 'self' "
            f"'nonce-{nonce}' "
            f"https://cdn.jsdelivr.net "
            f"https://www.gstatic.com "
            f"https://www.googleapis.com; "

            f"style-src 'self' "
            f"'unsafe-inline' "
            f"https://fonts.googleapis.com "
            f"https://cdn.jsdelivr.net; "

            f"img-src 'self' "
            f"data: blob: "
            f"https://res.cloudinary.com "
            f"https://*.cloudinary.com; "

            f"font-src 'self' "
            f"https://fonts.gstatic.com "
            f"https://cdn.jsdelivr.net; "

            f"connect-src 'self' "
            f"https://fcm.googleapis.com "
            f"https://firebaseinstallations.googleapis.com "
            f"https://api.cloudinary.com "
            f"https://*.cloudinary.com; "

            f"worker-src 'self' blob:; "

            f"frame-src https://www.google.com; "

            f"object-src 'none'; "
            f"base-uri 'self'; "
            f"form-action 'self'; "
            f"frame-ancestors 'none';"
        )

        return response