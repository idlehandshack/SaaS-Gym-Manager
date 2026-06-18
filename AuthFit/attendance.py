# AuthFit/attendance.py

from django.utils import timezone
from AuthFit.models import Enrollment, Attendence
import logging

logger = logging.getLogger(__name__)


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