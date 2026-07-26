# promos/urls.py
from django.urls import path

from . import views

urlpatterns = [
    path("promos/", views.list_active_promos, name="list-promos"),
    path("promos/create/", views.create_promo, name="create-promo"),
    path("promos/<int:promo_id>/toggle/", views.toggle_promo, name="toggle-promo"),
    path("promos/<int:promo_id>/delete/", views.delete_promo, name="delete-promo"),
]
