# billing/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("webhook/razorpay/", views.billing_webhook, name="billing-webhook"),
]
