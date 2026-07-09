# Shop/urls.py

from django.urls import path
from . import views
from . import device_views
from . import catalog_views
from . import admin_views
from . import dashboard_views

urlpatterns = [
    # ── Member-facing store ──────────────────────────────────────────────
    path('products/',                               views.product_list,    name='product_list'),
    path('product/<int:product_id>/',               views.product_detail,  name='product_detail'),
    path('product/<int:product_id>/confirm/',       views.confirm_order,   name='confirm_order'),
    path('order/place/',                            views.place_order,     name='place_order'),

    path('orders/success/<int:order_id>/',          views.order_success,   name='order_success'),
    path('orders/',                                 views.my_orders,       name='my_orders'),

    path('manage/orders/',                          views.order_dashboard, name='admin_orders'),
    path('manage/orders/<int:order_id>/update/',    views.order_update,    name='admin_order_update'),

    path('devices/register/',                       device_views.register_device,   name='register_device'),
    path('devices/unregister/',                     device_views.unregister_device, name='unregister_device'),

    # ── Gym-owner catalog: browse / import / create ──────────────────────
    path('catalog/',                                 catalog_views.catalog_browse,     name='catalog_browse'),
    path('catalog/<int:global_product_id>/import/',  catalog_views.catalog_import,     name='catalog_import'),
    path('catalog/create-new/',                       catalog_views.create_new_product, name='create_new_product'),

    # ── Gym-owner: manage own store ───────────────────────────────────────
    path('my-store/',                                 catalog_views.gym_store_manage, name='gym_store_manage'),
    path('my-store/<int:gym_product_id>/edit/',       catalog_views.gym_product_edit,  name='gym_product_edit'),
    path('my-store/<int:gym_product_id>/remove/',     catalog_views.gym_product_remove, name='gym_product_remove'),

    # ── Admin: catalog governance ─────────────────────────────────────────
    path('admin-catalog/pending/',                    admin_views.pending_products,  name='admin_pending_products'),
    path('admin-catalog/approved/',                   admin_views.approved_products, name='admin_approved_products'),
    path('admin-catalog/rejected/',                   admin_views.rejected_products, name='admin_rejected_products'),
    path('admin-catalog/<int:product_id>/approve/',   admin_views.approve_product,   name='admin_approve_product'),
    path('admin-catalog/<int:product_id>/reject/',    admin_views.reject_product,    name='admin_reject_product'),
    path('admin-catalog/<int:product_id>/edit/',      admin_views.edit_product,      name='admin_edit_product'),
    path('admin-catalog/merge/',                       admin_views.merge_products,    name='admin_merge_products'),
    path('my-store/flavor/<int:flavor_id>/stock/', catalog_views.stock_adjustment, name='stock_adjustment'),
    path('my-store/<int:gym_product_id>/restore/', catalog_views.gym_product_restore, name='gym_product_restore'),
    path('my-store/dashboard/', dashboard_views.product_dashboard, name='product_dashboard'),
    path('admin-catalog/', admin_views.admin_catalog_home, name='admin_catalog_home'),
]