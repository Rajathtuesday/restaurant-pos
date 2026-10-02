# printing/urls.py
from django.urls import path

from . import views

urlpatterns = [
    # The agent's key travels in the X-Agent-Key header (views.py). It used
    # to be part of these paths (/orders/agent/<key>/jobs/), which wrote the
    # secret into every access and proxy log; those paths are gone, and an
    # agent still using them gets a 404 until it is updated.
    path("orders/agent/add-job/", views.print_queue_add, name="print-queue-add"),
    path("orders/agent/jobs/", views.print_queue_poll, name="print-queue-poll"),
    path("orders/agent/done/<int:job_id>/", views.print_queue_done, name="print-queue-done"),
    path("orders/agent/failed/<int:job_id>/", views.print_queue_failed, name="print-queue-failed"),
]
