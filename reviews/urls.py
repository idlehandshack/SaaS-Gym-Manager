# reviews/urls.py
from django.urls import path
from . import views

app_name = 'reviews'

urlpatterns = [
    path('owner/review/', views.review_page, name='owner_review_page'),
    path('owner/review/generate/', views.generate_review, name='owner_review_generate'),
    path('owner/review/approve/', views.approve_review_view, name='owner_review_approve'),
]
