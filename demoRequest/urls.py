from django.urls import path
from . import views

app_name = "demoRequest"

urlpatterns = [
    path("request-demo/", views.request_demo_view, name="request_demo"),
    path("request-demo/success/", views.demo_request_success_view, name="success"),
]