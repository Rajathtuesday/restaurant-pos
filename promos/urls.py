# promos/urls.py
from django.urls import path

from . import views

# Creating, switching and archiving promos: setup/urls.py (the Setup screen).
urlpatterns = [
    path("promos/", views.list_active_promos, name="list-promos"),
]
