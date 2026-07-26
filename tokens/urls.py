# tokens/urls.py
from django.urls import path

from . import views

urlpatterns = [
    # Token System - Franchise and Cafe. Paths unchanged from their old
    # orders/urls.py location -- confirmed every template that links here
    # uses {% url %} names, not hardcoded paths, so this move is transparent
    # to the front end.
    path("token/", views.token_dashboard, name="token-dashboard"),
    path("token/new/", views.create_token_order, name="create-token-order"),
    path("token/go/", views.create_and_go_to_billing, name="create-and-bill"),
    path("token/<int:order_id>/bill/", views.token_billing, name="token-bill"),

    # Pickup readiness (QSR "Order Ready" board) -- staff-facing.
    path("token/<int:token_id>/ready/", views.mark_token_ready, name="mark-token-ready"),
    path("token/<int:token_id>/collected/", views.mark_token_collected, name="mark-token-collected"),

    # Public, unauthenticated display board -- meant for a TV/monitor at the
    # pickup counter, keyed by the outlet's permanent display_token secret.
    path("display/<uuid:display_token>/", views.display_board, name="display-board"),
    path("display/<uuid:display_token>/data/", views.display_data, name="display-data"),
]
