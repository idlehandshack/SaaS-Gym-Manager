# Gym/views_members.py
"""
Views for the Member Management Center.

Per the reusable-architecture requirement: this module contains NO
filtering, searching, sorting, or gym-scoping logic. Every query comes
from Gym.services.member_service — this file only reads request.GET,
forwards it to the service, paginates the result, and renders.
"""

from django.core.paginator import Paginator
from django.shortcuts import render
from django.shortcuts import get_object_or_404
from Gym.services.member_service import (
    get_member_detail_queryset,
    get_member_financial_summary,
    get_member_attendance_summary,
    get_member_activity_timeline,
    get_member_whatsapp_log,
    get_member_push_log,
)
from django.core.cache import cache
from AuthFit.models import Enrollment, MembershipPlan, Trainer
from Gym.dashboard_views import _staff_dashboard_required  # same access rule as the rest of the portal
from Gym.services.member_service import (
    FILTER_CHOICES,
    MEMBERSHIP_STATUS_CHOICES,
    SORT_CHOICES,
    get_member_queryset,
    get_member_stats,
)
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from AuthFit.attendance import mark_staff_attendance
from AuthFit.notifications import send_test_notification_to_member
MEMBERS_PER_PAGE = 25

@_staff_dashboard_required
@require_POST
def check_member_notifications(request, member_id):
    gym = request.gym
    member = get_object_or_404(get_member_detail_queryset(gym), pk=member_id)

    result = send_test_notification_to_member(member)
    return JsonResponse(result)

@_staff_dashboard_required
def member_list(request):
    gym = request.gym

    filter_key         = request.GET.get("filter", "all")
    search              = request.GET.get("search", "").strip()
    sort                = request.GET.get("sort", "newest")
    plan                = request.GET.get("plan") or None
    trainer             = request.GET.get("trainer") or None
    gender              = request.GET.get("gender") or None
    payment_status      = request.GET.get("payment_status") or None
    membership_status   = request.GET.get("membership_status") or None

    # ── The queryset — the ONLY call that produces member rows. ────────
    members_qs = get_member_queryset(
        gym=gym,
        filter=filter_key,
        search=search,
        sort=sort,
        plan=plan,
        trainer=trainer,
        gender=gender,
        payment_status=payment_status,
        membership_status=membership_status,
    )

    # ── Stats row — also built entirely from the service. ──────────────
    stats = get_member_stats(
        gym=gym,
        search=search,
        plan=plan,
        trainer=trainer,
        gender=gender,
        payment_status=payment_status,
    )

    paginator = Paginator(members_qs, MEMBERS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    # ── Filter dropdown data sources (gym-scoped, id + display name only —
    #    these populate <select> options, nothing else needs to load) ──
    plans    = MembershipPlan.objects.filter(gym=gym).order_by("plan").values("id", "plan")
    trainers = Trainer.objects.filter(gym=gym).order_by("name").values("id", "name")

    # Preserve every current querystring param except `page` when building
    # pagination links, so filters/search/sort survive page navigation.
    querystring = request.GET.copy()
    querystring.pop("page", None)

    context = {
        "gym": gym,
        "active": "member_management",  # distinct from the sidebar's existing
                                         # "members" value (used by Enrollment) —
                                         # left unlinked in the sidebar until
                                         # Dashboard Integration step.
        "page_obj": page_obj,
        "stats": stats,

        "filter_choices": FILTER_CHOICES,
        "sort_choices": SORT_CHOICES,
        "membership_status_choices": MEMBERSHIP_STATUS_CHOICES,
        "gender_choices": Enrollment.GENDER_CHOICES,
        "payment_status_choices": Enrollment.PAYMENT,
        "plans": plans,
        "trainers": trainers,

        "current_filter": filter_key,
        "current_search": search,
        "current_sort": sort,
        "current_plan": plan or "",
        "current_trainer": trainer or "",
        "current_gender": gender or "",
        "current_payment_status": payment_status or "",
        "current_membership_status": membership_status or "",

        "querystring": querystring.urlencode(),
    }
    return render(request, "dashboard/members/list.html", context)

@_staff_dashboard_required
def member_detail(request, member_id):
    gym = request.gym

    # get_member_detail_queryset already does gym-scoping + soft-delete
    # exclusion + the heavier prefetches this page needs (payments,
    # invoices, plan_change_logs) — same rule as the list view: this view
    # does no filtering/query-building of its own.
    member = get_object_or_404(get_member_detail_queryset(gym), pk=member_id)
    plans_key = f"membership_plans_{gym.pk}"
    plans = cache.get(plans_key)
    if plans is None:
        plans = list(MembershipPlan.objects.filter(gym=gym).values("id", "plan", "price", "duration_days"))
        cache.set(plans_key, plans, timeout=3600)
    context = {
        "gym": gym,
        "active": "member_management",
        "member": member,
        "plans": plans,
        "financial_summary": get_member_financial_summary(member),
        "attendance_summary": get_member_attendance_summary(member),
        "activity_timeline": get_member_activity_timeline(member),
        "whatsapp_log": get_member_whatsapp_log(member),
        "push_log": get_member_push_log(member),
        "invoices": member.invoices.all(),  # already prefetched, ordered -invoice_date
        "payments": member.payments.all(),  # already prefetched, ordered -payment_date
    }
    return render(request, "dashboard/members/detail.html", context)

@_staff_dashboard_required
@require_POST
def staff_mark_attendance(request, member_id):
    gym = request.gym

    # gym-scoping + soft-delete exclusion, same rule as member_detail
    member = get_object_or_404(get_member_detail_queryset(gym), pk=member_id)

    result = mark_staff_attendance(member, marked_by=request.user)

    status_map = {'success': 200, 'exists': 200, 'error': 400}
    return JsonResponse(result, status=status_map.get(result['status'], 400))