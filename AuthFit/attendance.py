# AuthFit/attendance.py
from django.db import transaction, IntegrityError
from AuthFit.models import Enrollment, Attendence, GymQRCode, AttendanceAttempt
import logging
logger = logging.getLogger(__name__)
from django.core.cache import cache
from django.utils import timezone

def mark_attendance(unique_id, gym_id=None):
    try:
        qs = Enrollment.objects.select_related('user').filter(
            unique_id=unique_id, is_deleted=False
        )
        if gym_id:
            qs = qs.filter(gym_id=gym_id)

        enrollment = qs.get()
        user       = enrollment.user
        today      = timezone.localdate()

        attendance, created = Attendence.objects.get_or_create(
            gym=enrollment.gym,
            enrollment=enrollment,
            date=today,
            defaults={'user': user},
        )

        if created:
            logger.info(
                "Attendance marked: user_id=%s unique_id=%s gym_id=%s date=%s",
                user.id, unique_id, enrollment.gym_id, today,
            )
            from AuthFit.views import _invalidate_attendance_cache
            _invalidate_attendance_cache(enrollment.gym_id)
            from notifications.attendance_broadcast import broadcast_attendance_marked
            broadcast_attendance_marked(enrollment, attendance, method='face')
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


def _qr_rate_limited(user_id, qr_token):
    key = f"qr_attempt_{user_id}_{qr_token[:16]}"
    count = cache.get(key, 0)
    if count >= 10:
        return True
    cache.set(key, count + 1, timeout=60)
    return False


def mark_qr_attendance(user, qr_token):

    today = timezone.localdate()

    if _qr_rate_limited(user.id, qr_token):
        logger.warning(
            "QR attendance rate-limited: user_id=%s token_prefix=%s",
            user.id, qr_token[:12],
        )
        return {'status': 'error', 'message': 'Too many attempts. Please wait a moment and try again.'}

    try:
        qr = GymQRCode.objects.select_related('gym').get(token=qr_token)
    except GymQRCode.DoesNotExist:
        logger.info("QR attendance invalid token: token_prefix=%s", qr_token[:12])
        AttendanceAttempt.objects.create(gym=None, user=user, reason='invalid_qr')
        return {'status': 'invalid_qr', 'message': 'This QR code is not valid for this gym.'}

    gym = qr.gym

    enrollment = Enrollment.objects.filter(
        user=user, gym=gym, is_deleted=False
    ).select_related('selectPlan').first()
    if not enrollment:
        logger.info(
            "QR attendance not enrolled: user_id=%s gym_id=%s token_prefix=%s",
            user.id, gym.id, qr_token[:12],
        )
        AttendanceAttempt.objects.create(gym=gym, user=user, reason='not_enrolled')
        return {'status': 'not_enrolled', 'message': 'You are not enrolled in this gym.'}

    if enrollment.is_expired:
        logger.info(
            "QR attendance expired plan: user_id=%s gym_id=%s enrollment_id=%s",
            user.id, gym.id, enrollment.id,
        )
        AttendanceAttempt.objects.create(gym=gym, user=user, enrollment=enrollment, reason='expired_plan')
        return {
            'status': 'expired_plan',
            'message': 'Your membership plan has expired. Please renew your plan to continue marking attendance.',
        }

    try:
        with transaction.atomic():
            attendance, created = Attendence.objects.get_or_create(
                gym=gym, enrollment=enrollment, date=today, defaults={'user': user},
            )
    except IntegrityError:
        # Lost a genuine concurrent race against the unique_together
        # constraint (double-tap, mobile retry storm). Winner already
        # created the row — treat this as "already marked", not an error.
        logger.info(
            "QR attendance concurrent race resolved as exists: user_id=%s gym_id=%s date=%s",
            user.id, gym.id, today,
        )
        return {'status': 'exists', 'message': 'Attendance already marked today.'}

    if created:
        cache.delete(f"today_attendance_{gym.pk}_{today}")
        from AuthFit.views import _invalidate_attendance_cache
        _invalidate_attendance_cache(gym.pk)
        logger.info(
            "QR attendance marked: user_id=%s gym_id=%s enrollment_id=%s date=%s method=qr",
            user.id, gym.id, enrollment.id, today,
        )
        from notifications.attendance_broadcast import broadcast_attendance_marked
        broadcast_attendance_marked(enrollment, attendance, method='qr')
        return {'status': 'success', 'message': 'Attendance marked successfully.'}

    return {'status': 'exists', 'message': 'Attendance already marked today.'}


def mark_staff_attendance(enrollment, marked_by=None, broadcast=True):
    """
    `broadcast` controls only the Live Attendance websocket toast — the
    Attendence row is created identically either way. Register Scan
    imports pass broadcast=False.
    """
    today = timezone.localdate()
    gym = enrollment.gym
    user = enrollment.user
    attendance, created = Attendence.objects.get_or_create(
        gym=gym, enrollment=enrollment, date=today, defaults={'user': user},
    )

    if created:
        cache.delete(f"today_attendance_{gym.pk}_{today}_Morning")
        cache.delete(f"today_attendance_{gym.pk}_{today}_Evening")
        from AuthFit.views import _invalidate_attendance_cache
        _invalidate_attendance_cache(gym.pk)
        logger.info(
            "Staff-marked attendance: enrollment_id=%s user_id=%s gym_id=%s date=%s marked_by=%s broadcast=%s",
            enrollment.id, getattr(user, 'id', None), gym.id, today, getattr(marked_by, 'id', None), broadcast,
        )
        if broadcast:
            from notifications.attendance_broadcast import broadcast_attendance_marked
            broadcast_attendance_marked(enrollment, attendance, method='staff')
        return {'status': 'success', 'message': f'Attendance marked for {enrollment.fullname}.'}

    return {'status': 'exists', 'message': f'{enrollment.fullname} is already marked present today.'}