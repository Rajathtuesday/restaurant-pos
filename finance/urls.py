# finance/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("expenses/", views.expense_list, name="expense_list"),
    path("expenses/create/", views.expense_create, name="expense_create"),
    path("expenses/<int:expense_id>/delete/", views.expense_delete, name="expense_delete"),
]
