# printing/urls.py
from django.urls import path

from . import views

urlpatterns = [
    # Paths deliberately unchanged from their old orders/urls.py location --
    # real print agents in the field poll these URLs directly (hardcoded in
    # the agent app), so the path itself must never move even though the app
    # serving it did.
    path("orders/agent/add-job/", views.print_queue_add, name="print-queue-add"),
    path("orders/agent/<uuid:agent_key>/jobs/", views.print_queue_poll, name="print-queue-poll"),
    path("orders/agent/<uuid:agent_key>/done/<int:job_id>/", views.print_queue_done, name="print-queue-done"),
    path("orders/agent/<uuid:agent_key>/failed/<int:job_id>/", views.print_queue_failed, name="print-queue-failed"),
]
