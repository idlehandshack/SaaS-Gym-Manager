# AuthFit/views_register_scan.py
#
# "Scan Attendance Register" endpoints. Gym-owner/receptionist only —
# reuses _gym_role_required from AuthFit.views, mirroring the rest of the
# staff-facing surface. No new attendance-writing logic — see
# AuthFit/services/register_scan_service.py. URLs/contracts from v1 are
# preserved; new fields are additive (import_id, quality_warnings, summary).
#
# NOTE: "time" has been removed from the whole flow (extraction, preview,
# validate, save, history) — attendance is stamped "now" on save.

import json
import logging
from Gym.ai_credit_service import has_credit
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST, require_GET

from AuthFit.views import _gym_role_required, get_client_ip
from AuthFit.models import RegisterScanImport
from AuthFit.services.register_scan_service import (
    extract_entries_from_image, build_preview_rows, search_members,
    validate_rows, save_rows, check_image_quality, create_pending_import,
    list_imports, get_import_detail, RegisterScanError,
)

logger = logging.getLogger(__name__)

ALLOWED_ROLES = ("gym_owner", "receptionist")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}


@_gym_role_required(*ALLOWED_ROLES)
@require_POST
def register_scan_upload(request):
    gym = getattr(request, "gym", None)
    if gym is None:
        return JsonResponse({"ok": False, "error": "No gym context available."}, status=403)

    # ── AI credit gate — must happen before any OpenAI call is made ──
    if not has_credit(gym):
        return JsonResponse({
            "ok": False,
            "error": "You don't have enough AI credits.",
            "error_code": "no_ai_credits",
        }, status=402)

    image = request.FILES.get("image")
    if not image:
        return JsonResponse({"ok": False, "error": "No image uploaded."}, status=400)

    if image.content_type not in ALLOWED_CONTENT_TYPES:
        return JsonResponse({"ok": False, "error": "Please upload a JPEG, PNG, or WEBP photo."}, status=400)

    if image.size > MAX_UPLOAD_BYTES:
        return JsonResponse({"ok": False, "error": "Image is too large (max 10MB)."}, status=400)

    try:
        image_bytes = image.read()
        quality_warnings = check_image_quality(image_bytes)

        entries = extract_entries_from_image(image_bytes, content_type=image.content_type)
        rows = build_preview_rows(gym, entries)

        image.seek(0)
        batch = create_pending_import(
            gym=gym, staff_user=request.user, ip_address=get_client_ip(request),
            image_file=image, raw_entries=entries, ai_count=len(entries),
        )

    except RegisterScanError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=422)
    except Exception:
        logger.exception("register_scan_upload failed for gym=%s", gym.pk)
        return JsonResponse({"ok": False, "error": "Something went wrong reading the register. Please retry."}, status=500)

    return JsonResponse({"ok": True, "rows": rows, "import_id": batch.id, "quality_warnings": quality_warnings})


@_gym_role_required(*ALLOWED_ROLES)
@require_GET
def register_scan_member_search(request):
    """GET /owner/attendance/register-scan/search-members/?q=..."""
    gym = getattr(request, "gym", None)
    if gym is None:
        return JsonResponse({"ok": False, "error": "No gym context available."}, status=403)

    query = request.GET.get("q", "")
    results = search_members(gym, query)
    return JsonResponse({"ok": True, "results": results})


@_gym_role_required(*ALLOWED_ROLES)
@require_POST
def register_scan_validate(request):
    """
    POST /owner/attendance/register-scan/validate/
    Body: {"rows": [{"client_id", "unique_id", "source"}, ...]}
    Returns: {"ok": true, "errors": {client_id: [messages]}}
    """
    gym = getattr(request, "gym", None)
    if gym is None:
        return JsonResponse({"ok": False, "error": "No gym context available."}, status=403)

    try:
        body = json.loads(request.body)
        rows = body.get("rows", [])
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    errors = validate_rows(gym, rows)
    return JsonResponse({"ok": True, "errors": errors})


@_gym_role_required(*ALLOWED_ROLES)
@require_POST
def register_scan_save(request):
    """
    POST /owner/attendance/register-scan/save/
    Body: {"rows": [...], "import_id": <optional int from /upload/>}

    Only rows that pass validation are saved (via the shared attendance
    service). Invalid rows are returned with per-row error messages so the
    owner can fix and resubmit — the whole import never gets rejected for
    one bad row. If import_id is present, that pending audit row is
    finalized in place instead of creating a duplicate.
    """
    gym = getattr(request, "gym", None)
    if gym is None:
        return JsonResponse({"ok": False, "error": "No gym context available."}, status=403)

    try:
        body = json.loads(request.body)
        rows = body.get("rows", [])
        import_id = body.get("import_id")
    except (ValueError, json.JSONDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON."}, status=400)

    if not rows:
        return JsonResponse({"ok": False, "error": "No rows to save."}, status=400)

    import_batch = None
    if import_id:
        import_batch = RegisterScanImport.objects.filter(gym=gym, pk=import_id).first()

    outcome = save_rows(gym, request.user, rows, import_batch=import_batch)
    summary = outcome["summary"]

    return JsonResponse({
        "ok": True,
        "errors": outcome["errors"],
        "results": outcome["results"],
        "batch_id": outcome["batch_id"],
        "saved_count": summary["saved"],
        "exists_count": summary["already_present"],
        "failed_count": summary["needs_review"],
        "summary": summary,
    })


# ──────────────────────────────────────────────────────────────────────────
# Import History
# ──────────────────────────────────────────────────────────────────────────
@_gym_role_required(*ALLOWED_ROLES)
@require_GET
def register_scan_history_page(request):
    """GET /owner/attendance/register-scan/history/ — renders the page shell."""
    gym = getattr(request, "gym", None)
    return render(request, "attendance_imports.html", {"gym": gym})


@_gym_role_required(*ALLOWED_ROLES)
@require_GET
def register_scan_history_list(request):
    """GET /owner/attendance/register-scan/history/list/ — JSON feed for the page."""
    gym = getattr(request, "gym", None)
    if gym is None:
        return JsonResponse({"ok": False, "error": "No gym context available."}, status=403)

    imports = list_imports(gym)
    data = [{
        "id": imp.id,
        "imported_by": imp.imported_by.get_full_name() or imp.imported_by.username if imp.imported_by else "—",
        "created_at": imp.created_at.strftime("%d %b %Y %I:%M %p"),
        "status": imp.status,
        "detected_count": imp.detected_count,
        "manual_count": imp.manual_count,
        "saved_count": imp.saved_count,
        "already_present_count": imp.already_present_count,
        "needs_review_count": imp.needs_review_count,
        "duration_ms": imp.duration_ms,
        "image_url": imp.image.url if imp.image else None,
        "credit_used": imp.credit_consumed,
    } for imp in imports]

    return JsonResponse({"ok": True, "imports": data})


@_gym_role_required(*ALLOWED_ROLES)
@require_GET
def register_scan_history_detail(request, import_id):
    """GET /owner/attendance/register-scan/history/<id>/ — raw+edited JSON for debugging."""
    gym = getattr(request, "gym", None)
    if gym is None:
        return JsonResponse({"ok": False, "error": "No gym context available."}, status=403)

    imp = get_import_detail(gym, import_id)
    if imp is None:
        return JsonResponse({"ok": False, "error": "Import not found."}, status=404)

    rows = [{
        "unique_id": r.unique_id,
        "confidence": r.confidence,
        "needs_review": r.needs_review,
        "source": r.source,
        "status": r.status,
        "error_message": r.error_message,
    } for r in imp.rows.all()]

    return JsonResponse({
        "ok": True,
        "id": imp.id,
        "imported_by": imp.imported_by.get_full_name() or imp.imported_by.username if imp.imported_by else "—",
        "ip_address": imp.ip_address,
        "created_at": imp.created_at.strftime("%d %b %Y %I:%M %p"),
        "image_url": imp.image.url if imp.image else None,
        "raw_ai_response": imp.raw_ai_response,
        "edited_response": imp.edited_response,
        "duration_ms": imp.duration_ms,
        "credit_used": imp.credit_consumed, 
        "rows": rows,
        "summary": {
            "detected_count": imp.detected_count,
            "manual_count": imp.manual_count,
            "rows_edited": imp.rows_edited,
            "saved_count": imp.saved_count,
            "already_present_count": imp.already_present_count,
            "needs_review_count": imp.needs_review_count,
        },
    })