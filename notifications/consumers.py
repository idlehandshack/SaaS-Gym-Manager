import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)


class AttendanceConsumer(AsyncWebsocketConsumer):
    """
    Per-gym attendance broadcast channel.

    Group name: attendance_gym_<gym_id>

    Multi-tenant safety: a connecting user is validated as an active
    gym_owner/receptionist StaffProfile for THIS specific gym_id BEFORE
    accept() is ever called. There is no fallback path and no shared
    group — a gym's owner can never join another gym's group even if
    they guess the URL.
    """

    async def connect(self):
        self.gym_id = self.scope['url_route']['kwargs']['gym_id']
        user = self.scope.get('user')

        if user is None or not user.is_authenticated:
            logger.warning(
                "AttendanceConsumer: rejected unauthenticated connection gym_id=%s",
                self.gym_id,
            )
            await self.close(code=4001)
            return

        allowed = await self._user_is_gym_staff(user, self.gym_id)
        if not allowed:
            logger.warning(
                "AttendanceConsumer: rejected user_id=%s for gym_id=%s "
                "(not an active owner/receptionist of this gym)",
                user.id, self.gym_id,
            )
            await self.close(code=4003)
            return

        self.group_name = f"attendance_gym_{self.gym_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        logger.info("AttendanceConsumer: connected user_id=%s gym_id=%s", user.id, self.gym_id)

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        # Client is push-only; nothing meaningful to receive. Ignore.
        pass

    # Dispatch target for group_send({"type": "attendance.notification", ...})
    async def attendance_notification(self, event):
        await self.send(text_data=json.dumps(event['payload']))

    @database_sync_to_async
    def _user_is_gym_staff(self, user, gym_id):
        if user.is_superuser:
            return True
        from Gym.models import StaffProfile
        return StaffProfile.objects.filter(
            user=user,
            gym_id=gym_id,
            active=True,
            role__in=['gym_owner', 'receptionist'],
        ).exists()