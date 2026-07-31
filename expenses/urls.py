from django.urls import path
from . import views

app_name = 'expenses'

urlpatterns = [
    path('', views.expense_dashboard, name='dashboard'),
    path('add/', views.add_expense, name='add'),
    path('list/', views.expense_list, name='list'),
    path('<int:pk>/edit/', views.edit_expense, name='edit'),
    path('<int:pk>/delete/', views.delete_expense, name='delete'),
]

# In the project's main urls.py, add:
#   path('expenses/', include(('expenses.urls', 'expenses'), namespace='expenses')),