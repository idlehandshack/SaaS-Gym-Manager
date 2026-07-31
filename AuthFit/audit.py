"""
AuthFit/audit.py
------------------
Single entry point for writing AuditLog rows. Every financial/administrative
mutation should call log_action() rather than constructing AuditLog directly,
so the shape of what gets logged never drifts between call sites.
"""
import logging

from Gym.models import AuditLog

logger = logging.getLogger(__name__)


def get_client_ip(request):
    """Mirrors AuthFit.views.get_client_ip — duplicated here deliberately
    to avoid a circular import (AuthFit.views already imports a lot)."""
    if request is None:
        return None
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def log_action(*, gym, action, staff_user=None, request=None,
                object_type='', object_id='', object_label='',
                old_values=None, new_values=None):
    """
    Writes one AuditLog row. Never raises — a logging failure must never
    take down the actual business operation it's describing (same
    isolation philosophy as _log_push_notification in notifications.py).

    `request` is optional — pass it when available to capture IP; some
    callers (cron jobs, management commands) have no request at all.
    """
    try:
        AuditLog.objects.create(
            gym=gym,
            staff_user=staff_user,
            action=action,
            object_type=object_type,
            object_id=str(object_id) if object_id else '',
            object_label=object_label,
            old_values=old_values or {},
            new_values=new_values or {},
            ip_address=get_client_ip(request) if request else None,
        )
    except Exception:
        logger.exception(
            "log_action failed — gym=%s action=%s object_type=%s object_id=%s",
            getattr(gym, 'pk', None), action, object_type, object_id,
        )