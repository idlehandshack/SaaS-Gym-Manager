# AuthFit/services/register_scan_service.py
#
# "Scan Attendance Register" — AI-assisted attendance import.
# Architecture unchanged: Photo → AI Vision → Editable Preview → Validation
# → existing attendance service (AuthFit.attendance.mark_attendance) →
# Attendance Database. This file owns extraction, matching, quality checks,
# validation, and the import audit trail. It never writes Attendence rows
# directly — see save_rows() → mark_attendance().
#
# NOTE: "time" has been intentionally removed from extraction/validation/
# save. We only read the ID column now (faster AI call, less to get wrong),
# and attendance is marked with the default timestamp (now) via
# mark_staff_attendance().

import base64
import io
import json
import logging
from datetime import datetime
from django.db import transaction
from Gym.ai_credit_service import deduct_credit
from openai import OpenAI
from django.conf import settings
from django.utils import timezone
from AuthFit.attendance import mark_staff_attendance
from AuthFit.models import Enrollment, Attendence, RegisterScanImport, RegisterScanImportRow
from AuthFit.attendance import mark_attendance
from notifications.attendance_broadcast import broadcast_register_scan_completed
logger = logging.getLogger(__name__)

OPENAI_MODEL = getattr(
    settings,
    "OPENAI_MODEL",
    "gpt-4.1-mini",
)

def get_openai_client():
    api_key = getattr(settings, "OPENAI_API_KEY", "")

    if not api_key:
        raise RegisterScanError(
            "AI service is not configured. Contact support."
        )

    return OpenAI(api_key=api_key)

CONFIDENCE_HIGH = 0.90
CONFIDENCE_MED = 0.70

EXTRACTION_PROMPT = """
Read the ID column ONLY from this handwritten gym attendance register
(ignore name/time/signature/headings). If rotated 90/180/270°, mentally
upright it first.

Read each ID digit-by-digit. Common confusions — check these before
committing:
- 6 vs 9: loop at BOTTOM + tail UP = 6. Loop at TOP + tail DOWN = 9.
  Judge by loop position + tail direction only, never by slant.
- 5 vs 8: 5 = flat/angular top + open curve. 8 = two closed loops.
- 1 vs 7: check for a top crossbar.
- 0 vs 6 vs 8: is the loop fully closed, and where's the tail?
- 3 vs 8: left side open (3) or closed (8)?
- 4 vs 9: top open (4) or closed loop (9)?
After reading every digit individually, re-read the complete ID from
left to right once to confirm no digit was accidentally changed or
silently corrected.
Confidence = honest, not optimistic:
0.90-1.0 clean & unambiguous | 0.70-0.89 minor smudge/ambiguity | <0.70 genuinely could be two digits.

Rules:
- Digits only, exactly as written. No inferred/"fixed" digits.
- Skip truly illegible rows entirely — don't guess.
- One row per line, in top-to-bottom order. Never merge/split/invent rows.
- Output ONLY this JSON, no markdown, no commentary:

{"entries":[{"unique_id":"1001","confidence":0.98}]}
"""


class RegisterScanError(Exception):
    pass


# ──────────────────────────────────────────────────────────────────────────
# Image quality pre-checks (cheap, PIL-only — runs before the AI call)
# ──────────────────────────────────────────────────────────────────────────
def check_image_quality(image_bytes: bytes) -> list:
    """
    Returns a list of human-readable warning strings (possibly empty).
    Never raises — a failed quality check should not block the AI call,
    it only informs the owner the photo might be worth retaking.
    """
    warnings = []
    try:
        from PIL import Image, ImageFilter, ImageStat

        img = Image.open(io.BytesIO(image_bytes))
        width, height = img.size

        if width < 600 or height < 400:
            warnings.append("This photo looks low-resolution — text may be hard to read. Consider retaking it closer up.")

        gray = img.convert("L")
        small = gray.resize((min(300, width), int(min(300, width) * height / width))) if width else gray

        brightness = ImageStat.Stat(small).mean[0]
        if brightness < 40:
            warnings.append("The register photo appears too dark. Results may be inaccurate.")
        elif brightness > 235:
            warnings.append("The register photo appears overexposed. Results may be inaccurate.")

        edges = small.filter(ImageFilter.FIND_EDGES)
        sharpness = ImageStat.Stat(edges).stddev[0]
        if sharpness < 8:
            warnings.append("The register photo appears blurry. Results may be inaccurate.")

    except Exception:
        logger.exception("Register scan: image quality check failed (non-fatal)")   

    return warnings


# ──────────────────────────────────────────────────────────────────────────
# Step 1 — AI extraction
# ──────────────────────────────────────────────────────────────────────────
def extract_entries_from_image(
    image_bytes: bytes,
    content_type: str = "image/jpeg",
) -> list:
    """
    Uses GPT-4.1 Mini Vision to extract attendance rows (ID only).
    """
    client = get_openai_client()
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    try:
        response = client.responses.create(
            model=OPENAI_MODEL,
            temperature=0,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": EXTRACTION_PROMPT,
                        },
                        {
                            "type": "input_image",
                            "image_url": (
                                f"data:{content_type};base64,{image_b64}"
                            ),
                        },
                    ],
                }
            ],
        )

        raw_text = (response.output_text or "").strip()
        if not raw_text:
            raise RegisterScanError(
                "The AI did not return any readable attendance data."
            )
    except RegisterScanError:
        raise
    except Exception:
        logger.exception("Register scan: OpenAI extraction call failed")
        raise RegisterScanError(
            "Could not process the image. Please try again."
        )

    try:

        parsed = json.loads(raw_text)

        entries = parsed.get("entries", [])

        if not isinstance(entries, list):
            raise ValueError

    except Exception:

        logger.warning(
            "Register scan: invalid JSON from OpenAI: %s",
            raw_text[:300],
        )

        raise RegisterScanError(
            "Could not read the register clearly. Please retake the photo."
        )

    cleaned = []

    for entry in entries:

        uid = str(entry.get("unique_id", "")).strip()

        if not uid:
            continue

        try:
            confidence = float(entry.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5

        confidence = max(0.0, min(1.0, confidence))

        cleaned.append(
            {
                "unique_id": uid,
                "confidence": round(confidence, 2),
            }
        )

    if not cleaned:
        raise RegisterScanError(
            "No attendance entries could be recognized on this page."
        )

    return cleaned


# ──────────────────────────────────────────────────────────────────────────
# Step 2 — match against this gym's Enrollment records
# ──────────────────────────────────────────────────────────────────────────
def confidence_tier(confidence) -> str:
    if confidence is None:
        return "unknown"
    if confidence >= CONFIDENCE_HIGH:
        return "high"
    if confidence >= CONFIDENCE_MED:
        return "medium"
    return "low"


def build_preview_rows(gym, entries: list) -> list:
    """
    Takes raw AI entries and attaches match info for the frontend preview.
    Every lookup is scoped to `gym` — never a cross-tenant query.
    """
    uids = [e["unique_id"] for e in entries]
    enrollments = {
        en.unique_id: en
        for en in Enrollment.objects.filter(gym=gym, unique_id__in=uids, is_deleted=False)
    }

    rows = []
    for idx, e in enumerate(entries):
        en = enrollments.get(e["unique_id"])
        confidence = e.get("confidence")
        row = {
            "client_id": f"ai-{idx}",
            "source": "ai",
            "unique_id": e["unique_id"],
            "confidence": confidence,
            "confidence_tier": confidence_tier(confidence),
            "needs_review": confidence is not None and confidence < CONFIDENCE_MED,
            "matched": en is not None,
            "member_name": en.fullname if en else "",
            "phone": en.phone if en else "",
            "is_expired": en.is_expired if en else None,
        }
        rows.append(row)
    return rows


def search_members(gym, query: str, limit: int = 10) -> list:
    """
    Powers the searchable member dropdown. Always gym-scoped.
    Supports: full/partial Unique ID, full/partial name, full/partial phone.
    "10" matches unique_id 1001/1012/2100; "Rah" matches Rahul/Rahman.
    """
    from django.db.models import Q, Case, When, Value, IntegerField
    query = (query or "").strip()
    if not query:
        return []

    qs = (
        Enrollment.objects
        .filter(gym=gym, is_deleted=False)
        .filter(Q(fullname__icontains=query) | Q(unique_id__icontains=query) | Q(phone__icontains=query))
        .annotate(
            # exact/prefix unique_id matches first, then name matches, then phone
            _rank=Case(
                When(unique_id=query, then=Value(0)),
                When(unique_id__istartswith=query, then=Value(1)),
                When(fullname__istartswith=query, then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            )
        )
        .order_by("_rank", "fullname")[:limit]
    )
    return [
        {"unique_id": en.unique_id, "name": en.fullname, "phone": en.phone}
        for en in qs
    ]


# ──────────────────────────────────────────────────────────────────────────
# Step 3 — validation (server-side, authoritative)
# ──────────────────────────────────────────────────────────────────────────
def validate_rows(gym, rows: list) -> dict:
    """
    rows: list of dicts with client_id, unique_id, source.
    Returns {client_id: [error strings]} for rows with problems.
    Does NOT mutate rows or touch the database.
    (Time is no longer collected or validated — attendance is marked
    with the default "now" timestamp.)
    """
    errors = {}
    seen_uids = {}
    today = timezone.localdate()

    for row in rows:
        cid = row.get("client_id")
        uid = (row.get("unique_id") or "").strip()
        row_errors = []

        if not uid:
            row_errors.append("Unique ID is required.")
        else:
            if uid in seen_uids:
                row_errors.append(f"Duplicate Unique ID in this import (also row {seen_uids[uid]}).")
            else:
                seen_uids[uid] = cid

        if uid:
            enrollment = Enrollment.objects.filter(gym=gym, unique_id=uid, is_deleted=False).first()
            if enrollment is None:
                row_errors.append("No member found with this Unique ID for this gym.")
            else:
                if enrollment.is_expired:
                    row_errors.append("This member's plan has expired.")
                if enrollment.user_id and Attendence.objects.filter(
                    gym=gym, user_id=enrollment.user_id, date=today
                ).exists():
                    row_errors.append("Attendance already marked today for this member.")
        if row_errors:
            errors[cid] = row_errors

    return errors


# ──────────────────────────────────────────────────────────────────────────
# Import lifecycle — audit trail (Import History)
# ──────────────────────────────────────────────────────────────────────────
def create_pending_import(gym, staff_user, ip_address, image_file, raw_entries, ai_count):
    """
    Called right after a successful AI extraction. Stores the original
    photo + raw AI output immediately, before the owner has edited or
    saved anything — so even an abandoned scan is auditable.
    Returns the RegisterScanImport instance.
    """
    image_public_id = ""
    try:
        import cloudinary.uploader
        image_file.seek(0)
        result = cloudinary.uploader.upload(image_file, folder="register_scans", resource_type="image")
        image_public_id = result.get("public_id", "")
    except Exception:
        logger.exception("Register scan: failed to store source photo (non-fatal)")

    batch = RegisterScanImport.objects.create(
        gym=gym,
        imported_by=staff_user,
        ip_address=ip_address,
        image_public_id=image_public_id,
        detected_count=ai_count,
        raw_ai_response=raw_entries,
        status="pending",
        started_at=timezone.now(),
    )
    return batch


def _rows_edited_count(raw_entries: list, rows: list) -> int:
    """How many AI rows were changed by the owner before saving (unique_id)."""
    raw_by_index = {f"ai-{i}": e for i, e in enumerate(raw_entries)}
    edited = 0
    for row in rows:
        if row.get("source") != "ai":
            continue
        original = raw_by_index.get(row.get("client_id"))
        if original is None:
            continue
        if row.get("unique_id") != original.get("unique_id"):
            edited += 1
    return edited


# ──────────────────────────────────────────────────────────────────────────
# Step 4 — save (delegates to the existing shared attendance service)
# ──────────────────────────────────────────────────────────────────────────
def save_rows(gym, staff_user, rows: list, import_batch=None) -> dict:
    start = timezone.now()
    errors = validate_rows(gym, rows)

    results = {}
    saved = already_present = failed = 0
    ai_count = sum(1 for r in rows if r.get("source") == "ai")
    manual_count = sum(1 for r in rows if r.get("source") == "manual")
    needs_review_count = sum(1 for r in rows if r.get("confidence_tier") == "low" or r.get("needs_review"))

    if import_batch is None:
        import_batch = RegisterScanImport.objects.create(
            gym=gym, imported_by=staff_user,
            detected_count=ai_count, manual_count=manual_count,
            status="pending", started_at=start,
        )

    for row in rows:
        cid = row.get("client_id")
        if cid in errors:
            continue  # invalid rows are skipped, not fatal to the batch

        uid = row["unique_id"].strip()
        enrollment = Enrollment.objects.filter(
            gym=gym, unique_id=uid, is_deleted=False
        ).first()

        if enrollment is None:
            result = {"status": "error", "message": "Member not found"}
        else:
            # No time is collected anymore — attendance is stamped "now"
            # by mark_staff_attendance's own default.
            result = mark_staff_attendance(
                enrollment=enrollment,
                marked_by=staff_user,
                broadcast=False,
            )

        results[cid] = result

        if result.get("status") == "success":
            row_status = "saved"
            saved += 1
        elif result.get("status") == "exists":
            row_status = "skipped_exists"
            already_present += 1
        else:
            row_status = "failed"
            failed += 1
            errors[cid] = [result.get("message", "Could not save this row.")]

        RegisterScanImportRow.objects.create(
            import_batch=import_batch,
            unique_id=uid,
            source=row.get("source", "ai"),
            status=row_status,
            confidence=row.get("confidence"),
            needs_review=bool(row.get("needs_review")),
            error_message="" if row_status != "failed" else result.get("message", ""),
        )

    still_broken = len([1 for r in rows if r.get("client_id") in errors])
    duration_ms = int((timezone.now() - start).total_seconds() * 1000)
    should_broadcast = False
    with transaction.atomic():
        locked_batch = RegisterScanImport.objects.select_for_update().get(pk=import_batch.pk)

        if saved > 0 and not locked_batch.credit_consumed:
            if deduct_credit(gym, reason="Register Scan", created_by=staff_user):
                locked_batch.credit_consumed = True

        if saved > 0 and not locked_batch.summary_broadcasted:
            locked_batch.summary_broadcasted = True
            should_broadcast = True

        locked_batch.manual_count = manual_count
        locked_batch.detected_count = ai_count if locked_batch.detected_count == 0 else locked_batch.detected_count
        locked_batch.saved_count = saved
        locked_batch.already_present_count = already_present
        locked_batch.failed_count = still_broken
        locked_batch.needs_review_count = needs_review_count
        locked_batch.edited_response = rows
        locked_batch.rows_edited = _rows_edited_count(locked_batch.raw_ai_response or [], rows)
        locked_batch.duration_ms = (
            duration_ms if not locked_batch.started_at
            else int((timezone.now() - locked_batch.started_at).total_seconds() * 1000)
        )
        locked_batch.status = "completed"
        locked_batch.save(update_fields=[
            "manual_count", "detected_count", "saved_count", "already_present_count",
            "failed_count", "needs_review_count", "edited_response", "rows_edited",
            "duration_ms", "status", "credit_consumed", "summary_broadcasted",
        ])
        if should_broadcast:
            total_rows = len(rows)
            def _send_summary(
                _gym=gym,
                _import_id=locked_batch.pk,
                _total=total_rows,
                _saved=saved,
                _duplicates=already_present,
                _failed=still_broken,
            ):
                today_count = Attendence.objects.filter(
                    gym=_gym,
                    date=timezone.localdate(),
                ).count()

                broadcast_register_scan_completed(
                    gym=_gym,
                    import_id=_import_id,
                    total=_total,
                    imported=_saved,
                    duplicates=_duplicates,
                    failed=_failed,
                    attendance_today=today_count,
                )
            transaction.on_commit(_send_summary)
    return {
        "errors": errors,
        "results": results,
        "batch_id": import_batch.id,
        "summary": {
            "saved": saved,
            "already_present": already_present,
            "needs_review": still_broken,
            "manual_entries": manual_count,
            "ai_entries": ai_count,
        },
    }
# ──────────────────────────────────────────────────────────────────────────
# Import History
# ──────────────────────────────────────────────────────────────────────────
def list_imports(gym, limit=50):
    return (
        RegisterScanImport.objects
        .filter(gym=gym)
        .select_related("imported_by")
        .order_by("-created_at")[:limit]
    )
def get_import_detail(gym, import_id):
    return (
        RegisterScanImport.objects
        .filter(gym=gym, pk=import_id)
        .select_related("imported_by")
        .prefetch_related("rows")
        .first()
    )