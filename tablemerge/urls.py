# tablemerge/urls.py
from django.urls import path

from . import views

urlpatterns = [
    # Paths unchanged from their old orders/urls.py location -- tables.html
    # (the only template referencing them) uses {% url %} names, never
    # hardcoded paths, so this move is transparent to the front end.
    path("merge-tables/", views.merge_tables_view, name="merge-tables"),
    path("unmerge-tables/<int:primary_id>/", views.unmerge_tables_view, name="unmerge-tables"),
]
