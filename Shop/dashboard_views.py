# Shop/dashboard_views.py

from django.shortcuts import render
from Gym.mixins import gym_staff_required
from Gym.decorators import store_enabled_required
from .services import ProductDashboardService


@gym_staff_required
@store_enabled_required
def product_dashboard(request):
    gym = getattr(request, 'gym', None)

    summary = ProductDashboardService.get_summary(gym)
    most_sold = ProductDashboardService.most_sold(gym, limit=10)
    least_sold = ProductDashboardService.least_sold(gym, limit=10)
    never_sold = ProductDashboardService.never_sold(gym)[:10]
    low_stock = ProductDashboardService.low_stock_flavors(gym)
    out_of_stock = ProductDashboardService.out_of_stock_flavors(gym)

    return render(request, 'shop/admin/product_dashboard.html', {
        'gym': gym,
        'summary': summary,
        'most_sold': most_sold,
        'least_sold': least_sold,
        'never_sold': never_sold,
        'low_stock': low_stock,
        'out_of_stock': out_of_stock,
    })