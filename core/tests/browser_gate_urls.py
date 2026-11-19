"""
Addresses for core.tests.test_browser_server_gate only: a page, and an
answer that spends a while in Python before it touches the database (the
window in which Postgres sees nothing running).
"""
import threading
import time

from django.contrib.auth import get_user_model
from django.http import HttpResponse, JsonResponse
from django.urls import path

FINISHED = threading.Event()
SLOW_SECONDS = 1.5


def page(request):
    return HttpResponse("<!doctype html><title>gate</title><p>gate</p>")


def slow(request):
    time.sleep(SLOW_SECONDS)
    users = get_user_model().objects.count()
    FINISHED.set()
    return JsonResponse({"users": users})


urlpatterns = [
    path("page/", page),
    path("slow/", slow),
]
