# AuthFit/attendance.py

from django.utils import timezone
from AuthFit.models import Enrollment, Attendence ,GymQRCode, AttendanceAttempt
import logging
logger = logging.getLogger(__name__)
from django.core.cache import cache

def mark_attendance(unique_id, gym_id=None):
    try:
        qs = Enrollment.objects.select_related('user').filter(unique_id=unique_id)
        if gym_id:
            qs = qs.filter(gym_id=gym_id)

        enrollment = qs.get()
        user       = enrollment.user
        today      = timezone.localdate()

        attendance, created = Attendence.objects.get_or_create(
            user=user,
            date=today,
            gym=enrollment.gym,      # ← required field, scoped correctly
        )

        if created:
            logger.info(
                "Attendance marked: user_id=%s unique_id=%s gym_id=%s date=%s",
                user.id, unique_id, enrollment.gym_id, today,
            )
            return {"status": "success", "message": "Attendance marked successfully"}
        else:
            return {"status": "exists", "message": "Attendance already marked today"}

    except Enrollment.DoesNotExist:
        logger.warning(
            "Attendance failed — enrollment not found: unique_id=%s gym_id=%s",
            unique_id, gym_id,
        )
        return {"status": "error", "message": "Member not found"}

    except Enrollment.MultipleObjectsReturned:
        logger.error(
            "Attendance failed — duplicate unique_id: unique_id=%s gym_id=%s",
            unique_id, gym_id,
        )
        return {"status": "error", "message": "Duplicate member ID — contact support"}

    except Exception:
        logger.exception(
            "Unexpected error in mark_attendance: unique_id=%s gym_id=%s",
            unique_id, gym_id,
        )
        return {"status": "error", "message": "An internal error occurred"}
    
def mark_qr_attendance(user, qr_token):
    """
    Validation order per spec: QR exists → belongs to a gym → user enrolled
    → membership active → not already marked today → create attendance.
    (Auth check happens in the view, same as the geo path.)
    """
    today = timezone.localdate()

    try:
        qr = GymQRCode.objects.select_related('gym').get(token=qr_token)
    except GymQRCode.DoesNotExist:
        AttendanceAttempt.objects.create(gym=None, user=user, reason='invalid_qr')
        return {'status': 'invalid_qr', 'message': 'This QR code is not valid for this gym.'}

    gym = qr.gym

    enrollment = Enrollment.objects.filter(user=user, gym=gym).select_related('selectPlan').first()
    if not enrollment:
        AttendanceAttempt.objects.create(gym=gym, user=user, reason='not_enrolled')
        return {'status': 'not_enrolled', 'message': 'You are not enrolled in this gym.'}

    if enrollment.is_expired:
        AttendanceAttempt.objects.create(gym=gym, user=user, enrollment=enrollment, reason='expired_plan')
        return {
            'status': 'expired_plan',
            'message': 'Your membership plan has expired. Please renew your plan to continue marking attendance.',
        }

    attendance, created = Attendence.objects.get_or_create(user=user, date=today, gym=gym)

    if created:
        cache.delete(f"today_attendance_{gym.pk}_{today}")
        logger.info("QR attendance marked: user_id=%s gym_id=%s date=%s", user.id, gym.id, today)
        return {'status': 'success', 'message': 'Attendance marked successfully.'}

    return {'status': 'exists', 'message': 'Attendance already marked today.'}