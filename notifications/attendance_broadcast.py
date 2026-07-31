"""
Single entry point for broadcasting a "attendance just happened" event to a
gym's connected staff dashboards. Called AFTER attendance is successfully
saved — never before, never speculatively.

Usage from any of the three attendance paths (face / qr / geo):
    from notifications.attendance_broadcast import broadcast_attendance_marked
    if created:
        broadcast_attendance_marked(enrollment, attendance, method='face')
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from cloudinary.utils import cloudinary_url
from django.utils import timezone

logger = logging.getLogger(__name__)

METHOD_LABELS = {
    'face': ('FACE', 'success'),
    'qr':   ('QR', 'primary'),
    'geo':  ('GPS', 'warning'),
}


def broadcast_attendance_marked(enrollment, attendance, method: str) -> None:
    """
    Fire-and-forget websocket broadcast, gym-scoped.

    enrollment: the Enrollment row for the member who just attended
                (must have selectPlan populated or accessible).
    attendance: the Attendence row that was just created.
    method:     'face' | 'qr' | 'geo'

    Never raises. A broadcast failure (Redis hiccup, missing channel layer,
    bad image URL, etc.) must never break the attendance flow that already
    succeeded and was already committed to the database.
    """
    try:
        gym = enrollment.gym
        if gym is None:
            return

        # ── Member photo (best-effort, same pattern as today_attendance view) ──
        image_url = None
        if enrollment.face_image:
            try:
                public_id = (
                    enrollment.face_image.public_id
                    if hasattr(enrollment.face_image, "public_id")
                    else str(enrollment.face_image)
                )
                if public_id:
                    image_url, _ = cloudinary_url(
                        public_id, width=80, height=80,
                        crop="fill", gravity="face",
                        fetch_format="auto", quality="auto", secure=True,
                    )
            except Exception:
                logger.exception(
                    "broadcast_attendance_marked: cloudinary url failed enrollment=%s",
                    enrollment.id,
                )

        # ── Days-remaining badge ─────────────────────────────────────────
        days_remaining = enrollment.days_remaining
        if enrollment.is_expired:
            days_label, days_color = "Expired Membership", "danger"
        elif days_remaining is not None and days_remaining <= 3:
            days_label, days_color = f"{days_remaining} Days Remaining", "warning"
        elif days_remaining is not None:
            days_label, days_color = f"{days_remaining} Days Remaining", "success"
        else:
            days_label, days_color = "—", "secondary"

        method_label, method_color = METHOD_LABELS.get(method, ('—', 'secondary'))

        payload = {
            "attendance_id":        attendance.id,
            "member_id":            enrollment.id,
            "unique_id":            enrollment.unique_id,
            "photo":                image_url,
            "gender":               enrollment.gender,
            "name":                 enrollment.fullname,
            "phone":                enrollment.phone,
            "plan":                 enrollment.selectPlan.plan if enrollment.selectPlan else "—",
            "method":               method,
            "method_label":         method_label,
            "method_color":         method_color,
            "attendance_time":      timezone.localtime().strftime("%I:%M %p"),
            "days_remaining_label": days_label,
            "days_remaining_color": days_color,
            "payment_status":       enrollment.paymentStatus,
            "gym_id":               str(gym.id),
        }

        channel_layer = get_channel_layer()
        if channel_layer is None:
            logger.warning("broadcast_attendance_marked: no channel layer configured, skipping")
            return

        async_to_sync(channel_layer.group_send)(
            f"attendance_gym_{gym.id}",
            {"type": "attendance.notification", "payload": payload},
        )
        logger.info(
            "Attendance broadcast sent gym=%s enrollment=%s method=%s",
            gym.id, enrollment.id, method,
        )

    except Exception:
        logger.exception(
            "broadcast_attendance_marked: failed for enrollment=%s method=%s",
            getattr(enrollment, 'id', None), method,
        )

def broadcast_register_scan_completed(gym, import_id, total: int, imported: int, duplicates: int, failed: int, attendance_today: int) -> None:
    """
    Fire-and-forget websocket broadcast, gym-scoped — the Register Scan
    equivalent of broadcast_attendance_marked(), sent ONCE per import.
    Individual mark_staff_attendance() calls during the import pass
    broadcast=False so this summary is the only websocket traffic a bulk
    import generates. Duplicate-broadcast prevention (summary_broadcasted
    flag + row lock) and commit-safety (transaction.on_commit) are the
    caller's responsibility in register_scan_service.save_rows().

    Never raises, for the same reason as broadcast_attendance_marked():
    the import is already committed by the time this runs.
    """
    try:
        if gym is None:
            return

        payload = {
            "type":             "register_scan_completed",
            "import_id":        import_id,
            "total":            total,
            "imported":         imported,
            "duplicates":       duplicates,
            "failed":           failed,
            "attendance_today": attendance_today,
            "completed_at":     timezone.localtime().isoformat(),
            "time":             timezone.localtime().strftime("%H:%M"),
            "gym_id":           str(gym.id),
        }

        channel_layer = get_channel_layer()
        if channel_layer is None:
            logger.warning("broadcast_register_scan_completed: no channel layer configured, skipping")
            return

        async_to_sync(channel_layer.group_send)(
            f"attendance_gym_{gym.id}",
            {"type": "attendance.notification", "payload": payload},
        )
        logger.info(
            "Register Scan completion broadcast sent gym=%s import_id=%s imported=%s duplicates=%s failed=%s",
            gym.id, import_id, imported, duplicates, failed,
        )

    except Exception:
        logger.exception(
            "broadcast_register_scan_completed: failed for gym=%s import_id=%s",
            getattr(gym, 'id', None), import_id,
        )