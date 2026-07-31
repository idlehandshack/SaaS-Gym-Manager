# notifications/routing.py
from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    re_path(
        r'^ws/attendance/(?P<gym_id>[0-9a-f-]+)/$',  # CHANGED — was \d+, now matches UUID format
        consumers.AttendanceConsumer.as_asgi()
    ),
]