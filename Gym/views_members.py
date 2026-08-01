# Gym/views_members.py
from django.core.paginator import Paginator,EmptyPage
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
# same access rule as the rest of the portal
from Gym.dashboard_views import _staff_dashboard_required
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

    filter_key = request.GET.get("filter", "all")
    search = request.GET.get("search", "").strip()
    sort = request.GET.get("sort", "newest")
    plan = request.GET.get("plan") or None
    trainer = request.GET.get("trainer") or None
    gender = request.GET.get("gender") or None
    payment_status = request.GET.get("payment_status") or None
    membership_status = request.GET.get("membership_status") or None
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
    stats = get_member_stats(
        gym=gym,
        search=search,
        plan=plan,
        trainer=trainer,
        gender=gender,
        payment_status=payment_status,
    )

    paginator = Paginator(members_qs, MEMBERS_PER_PAGE)
    requested_page = request.GET.get("page", 1)
    try:
        page_obj = paginator.get_page(requested_page)
    except EmptyPage:
        page_obj = paginator.get_page(1)
    plans = MembershipPlan.objects.filter(
        gym=gym).order_by("plan").values("id", "plan")
    trainers = Trainer.objects.filter(
        gym=gym).order_by("name").values("id", "name")
    querystring = request.GET.copy()
    querystring.pop("page", None)

    context = {
        "gym": gym,
        "active": "member_management",
        "stats": stats,
        "page_obj": page_obj,
        "members": page_obj.object_list,
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
    member = get_object_or_404(get_member_detail_queryset(gym), pk=member_id)
    plans_key = f"membership_plans_{gym.pk}"
    plans = cache.get(plans_key)
    if plans is None:
        plans = list(MembershipPlan.objects.filter(gym=gym).values(
            "id", "plan", "price", "duration_days"))
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
        "invoices": member.invoices.all(),
        "payments": member.payments.all(),
    }
    return render(request, "dashboard/members/detail.html", context)


@_staff_dashboard_required
@require_POST
def staff_mark_attendance(request, member_id):
    gym = request.gym
    member = get_object_or_404(get_member_detail_queryset(gym), pk=member_id)

    result = mark_staff_attendance(member, marked_by=request.user)

    status_map = {'success': 200, 'exists': 200, 'error': 400}
    return JsonResponse(result, status=status_map.get(result['status'], 400))
