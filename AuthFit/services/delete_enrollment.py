import logging
from django.db import transaction
from django.core.cache import cache
from django.utils import timezone
import cloudinary.uploader

from AuthFit.models import Enrollment, Attendence, EnrollmentDeletionLog

logger = logging.getLogger(__name__)


class DeleteEnrollmentError(Exception):
    """Raised for validation/authorization failures — caller returns 403/400."""
    pass


def _clear_enrollment_related_cache(enrollment):
    uid = enrollment.user_id
    gym_pk = enrollment.gym_id
    cache.delete(f"enrollment_status_{uid}_{gym_pk}")
    cache.delete(f"enrolled_{uid}_{gym_pk}")
    cache.delete(f"enrollment_{uid}_{gym_pk}")
    cache.delete(f"profile_image_{uid}")
    cache.delete(f"admin_revenue_{gym_pk}")
    cache.delete(f"face_users_{gym_pk}")


def _delete_face_image(enrollment):
    """Never leave an orphan Cloudinary asset behind."""
    if not enrollment.face_image:
        return
    try:
        public_id = (
            enrollment.face_image.public_id
            if hasattr(enrollment.face_image, "public_id")
            else str(enrollment.face_image)
        )
        if public_id:
            cloudinary.uploader.destroy(public_id)
    except Exception:
        logger.exception("Cloudinary cleanup failed for enrollment_id=%s", enrollment.id)


def _log_deletion(enrollment, gym, acting_user, delete_type, reason):
    EnrollmentDeletionLog.objects.create(
        gym=gym,
        gym_owner=acting_user,
        enrollment_id=enrollment.id,
        member_name=enrollment.fullname,
        member_phone=enrollment.phone,
        delete_type=delete_type,
        reason=reason or "",
    )


def _assert_owned_by_gym(enrollment, gym):
    if enrollment.gym_id != gym.id:
        # Never allow one gym to touch another gym's enrollment.
        raise DeleteEnrollmentError("Enrollment does not belong to this gym.")


def delete_enrollment_duplicate(enrollment, gym, acting_user, reason=""):
    """
    Permanently removes an enrollment and every related record.
    Intended ONLY for genuine duplicates (e.g. a Quick Enrollment that was
    later re-registered by the member under a different phone number).
    """
    _assert_owned_by_gym(enrollment, gym)

    # Local import avoids a circular import between AuthFit and billing.
    from billing.models import Invoice, Payment

    # Log before deleting — enrollment_id/name/phone are snapshotted, not FK'd.
    _log_deletion(enrollment, gym, acting_user, 'duplicate', reason)

    with transaction.atomic():
        _delete_face_image(enrollment)

        if enrollment.user_id:
            Attendence.objects.filter(gym=gym, user_id=enrollment.user_id).delete()

            try:
                from AuthFit.models import UserDevice
                UserDevice.objects.filter(gym=gym, user_id=enrollment.user_id).delete()
            except Exception:
                logger.exception(
                    "UserDevice cleanup failed for enrollment_id=%s", enrollment.id
                )

        Payment.objects.filter(gym=gym, enrollment=enrollment).delete()
        Invoice.objects.filter(gym=gym, member=enrollment).delete()

        try:
            from AuthFit.models import EnrollmentTransfer
            EnrollmentTransfer.objects.filter(
                gym or None, previous_enrollment=enrollment
            ).update(previous_enrollment=None) if False else None
            EnrollmentTransfer.objects.filter(
                previous_enrollment=enrollment
            ).update(previous_enrollment=None)
        except Exception:
            logger.exception(
                "EnrollmentTransfer cleanup failed for enrollment_id=%s", enrollment.id
            )

        _clear_enrollment_related_cache(enrollment)
        enrollment.delete()

    logger.info(
        "Duplicate enrollment permanently deleted — gym=%s enrollment_id=%s by=%s reason=%r",
        gym.pk, enrollment.id, getattr(acting_user, 'username', None), reason,
    )


def delete_enrollment_soft(enrollment, gym, acting_user, reason=""):
    """
    Soft-deletes an enrollment. Payments, invoices, and all financial /
    audit history stay intact — the enrollment is simply hidden from
    active-member views everywhere in the app.
    """
    _assert_owned_by_gym(enrollment, gym)

    _log_deletion(enrollment, gym, acting_user, 'soft', reason)

    with transaction.atomic():
        enrollment.is_deleted = True
        enrollment.deleted_at = timezone.now()
        enrollment.deleted_by = acting_user
        enrollment.save(update_fields=['is_deleted', 'deleted_at', 'deleted_by'])
        _clear_enrollment_related_cache(enrollment)

    logger.info(
        "Enrollment soft-deleted — gym=%s enrollment_id=%s by=%s reason=%r",
        gym.pk, enrollment.id, getattr(acting_user, 'username', None), reason,
    )