from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Sum
from django.shortcuts import render
from AuthFit.models import Enrollment
from .models import Gym

@staff_member_required
def saas_dashboard(request):
    gyms = Gym.objects.select_related('plan', 'owner').annotate(
        member_count=Count('enrollment'),
        revenue=Sum('enrollment__Amount'),
    ).order_by('-created_at')

    return render(request, 'gym/saas_dashboard.html', {
        'gyms': gyms,
        'total_gyms': gyms.count(),
        'active_gyms': gyms.filter(active=True).count(),
    })