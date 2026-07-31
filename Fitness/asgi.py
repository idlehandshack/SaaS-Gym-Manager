"""
ASGI config for Fitness project — now serves both HTTP and WebSocket.
"""
import os
import sys
import asyncio

# NEW — Windows' default ProactorEventLoop has known incompatibilities with
# channels_redis' async socket reads (manifests as spurious read timeouts
# even against a healthy local Redis). SelectorEventLoop doesn't have this
# issue. Only applies on Windows; no effect on Linux (droplet/prod).
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Fitness.settings')
django.setup()

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

django_asgi_app = get_asgi_application()

import notifications.routing

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(notifications.routing.websocket_urlpatterns)
        )
    ),
})