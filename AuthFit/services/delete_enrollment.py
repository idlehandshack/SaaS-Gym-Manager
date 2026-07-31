# AuthFit/services/delete_enrollment.py

import logging
from django.db import transaction
from django.core.cache import cache
from django.utils import timezone
import cloudinary.uploader
from AuthFit.models import  Attendence, EnrollmentDeletionLog

logger = logging.getLogger(__name__)
class DeleteEnrollmentError(Exception):
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
        raise DeleteEnrollmentError("Enrollment does not belong to this gym.")


def delete_enrollment_duplicate(enrollment, gym, acting_user, reason=""):
    _assert_owned_by_gym(enrollment, gym)

    from billing.models import Invoice, Payment
    from AuthFit.audit import log_action

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
        invoices = list(
            Invoice.objects.select_for_update()
            .filter(gym=gym, member=enrollment)
            .exclude(status=Invoice.Status.VOID)
        )
        for invoice in invoices:
            old_status = invoice.status
            invoice.status = Invoice.Status.VOID
            invoice.cancellation_reason = (
                f"Duplicate enrollment deleted (enrollment_id={enrollment.id}). {reason}".strip()
            )
            invoice.save(update_fields=["status", "cancellation_reason", "updated_at"])
            log_action(
                gym=gym,
                action='invoice_voided',
                staff_user=acting_user,
                request=None,
                object_type='Invoice',
                object_id=invoice.pk,
                object_label=invoice.invoice_number,
                old_values={"status": old_status},
                new_values={"status": invoice.status},
            )

        # Payment has no void/status field yet — left intact deliberately.
        # It's a pure cash-ledger snapshot; voiding the linked Invoice is
        # what excludes the amount from RevenueService's aggregates.

        try:
            from AuthFit.models import EnrollmentTransfer
            EnrollmentTransfer.objects.filter(
                previous_enrollment=enrollment
            ).update(previous_enrollment=None)
        except Exception:
            logger.exception(
                "EnrollmentTransfer cleanup failed for enrollment_id=%s", enrollment.id
            )

        log_action(
            gym=gym,
            action='enrollment_deleted',
            staff_user=acting_user,
            request=None,
            object_type='Enrollment',
            object_id=enrollment.pk,
            object_label=enrollment.fullname,
            old_values={},
            new_values={"delete_type": "duplicate", "reason": reason},
        )

        _clear_enrollment_related_cache(enrollment)
        enrollment.delete()

    logger.info(
        "Duplicate enrollment deleted (financial records voided, not removed) — "
        "gym=%s enrollment_id=%s by=%s reason=%r",
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