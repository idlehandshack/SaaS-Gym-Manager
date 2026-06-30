# §2  AuthFit/signals.py

import logging
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
 
logger = logging.getLogger(__name__)
 
# Cache TTL for the version key (seconds).
# Short enough to surface new enrollments quickly; long enough to absorb
# burst polls from multiple cameras in the same gym.
_VERSION_CACHE_TTL = 30
 
 
def _version_cache_key(gym_id: str) -> str:
    """Centralised key builder — one place to change the format."""
    return f"embedding_version_{gym_id}"
 
 
def _bump_gym_version(gym_id, *, reason: str = "") -> None:
    """
    Atomically increment Gym.embedding_version AND invalidate the version
    cache so the next /api/embedding-version/ poll reflects the new value
    immediately.
 
    This is the SINGLE source of truth for both operations.
    No other code should touch the version cache directly.
 
    Runs inside the caller's transaction so the increment is rolled back
    if the triggering save is rolled back.
    """
    if gym_id is None:
        return
 
    try:
        # Import inside function to avoid circular imports at module load.
        from Gym.models import Gym  # Issue 2: correct app import
 
        rows_updated = Gym.objects.filter(pk=gym_id).update(
            embedding_version=F("embedding_version") + 1
        )
 
        if rows_updated:
            cache_key = _version_cache_key(gym_id)
            cache.delete(cache_key)  # Issue 1: always invalidated here
            logger.info(
                "embedding_version bumped — gym_id=%s  reason=%s  cache_key=%s",
                gym_id, reason, cache_key,
            )
        else:
            logger.warning(
                "embedding_version bump had no effect — gym_id=%s not found  reason=%s",
                gym_id, reason,
            )
 
    except Exception:
        logger.exception(
            "Failed to bump embedding_version for gym_id=%s", gym_id
        )
 
 
# ── Public helper — use this for every embedding write (Issue 3) ──────────────
 
def update_enrollment_embeddings(
    enrollment,
    new_embeddings: list,
    *,
    max_stored: int = 7,
) -> list:
    """
    Centralised helper for all operations that modify face_embeddings.
 
    Usage (in any view or management command):
 
        from AuthFit.signals import update_enrollment_embeddings
 
        updated = update_enrollment_embeddings(enrollment, [emb1, emb2])
        # enrollment is already saved; version bump already happened.
 
    Returns the updated face_embeddings list (for response bodies, logging, etc.).
 
    Benefits:
      • Callers never need to know which fields are "face-related".
      • The signal fires reliably with correct update_fields.
      • Adding a new face field in the future only requires adding it here.
    """
    face_embeddings = list(enrollment.face_embeddings or [])
 
    for emb in new_embeddings:
        if len(face_embeddings) >= max_stored:
            face_embeddings.pop(0)          # FIFO eviction of oldest embedding
        face_embeddings.append(emb)
 
    enrollment.face_embeddings = face_embeddings
    enrollment.face_enrolled   = True
 
    # Explicit update_fields tells the post_save signal exactly what changed,
    # allowing it to skip spurious bumps on unrelated saves.
    enrollment.save(update_fields=["face_embeddings", "face_enrolled"])
 
    # face_users cache is separate from the version cache; invalidate it here
    # so get_users() returns fresh data on the very next request.
    cache.delete(f"face_users_{enrollment.gym_id}")
 
    return face_embeddings
 
 
# ── Signals ───────────────────────────────────────────────────────────────────
 
# The set of field names that, when changed, warrant a version bump.
# Keep this set in sync with the Enrollment model's face fields.
# If you add a new face-related field, add it here — nothing else changes.
_FACE_FIELDS = frozenset({"face_embeddings", "face_enrolled", "face_image"})
 
 
@receiver(post_save, sender="AuthFit.Enrollment")
def _on_enrollment_saved(sender, instance, created, update_fields, **kwargs):
    """
    Bump the gym's embedding version when a face-related field is written.
 
    • New enrollment → always bump (a new face slot was created).
    • Full .save() (update_fields=None) → bump conservatively; we cannot
      know which fields changed, so we treat it as potentially face-related.
    • Partial .save() with update_fields → bump only if at least one
      face-related field is included.
    """
    if created:
        _bump_gym_version(instance.gym_id, reason="enrollment_created")
        return
 
    if update_fields is None or _FACE_FIELDS.intersection(update_fields):
        _bump_gym_version(instance.gym_id, reason="enrollment_face_updated")
 
 
@receiver(post_delete, sender="AuthFit.Enrollment")
def _on_enrollment_deleted(sender, instance, **kwargs):
    """A deleted member always changes the recognition universe."""
    _bump_gym_version(instance.gym_id, reason="enrollment_deleted")
 
 