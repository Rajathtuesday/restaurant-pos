# payments/urls.py
from django.urls import path

from . import razorpay_views, refund_views

urlpatterns = [
    # Paths unchanged from their old orders/urls.py location -- confirmed
    # every template reference (bill.html, order_history.html) and
    # setup/views/core_views.py's reverse('razorpay-webhook') call use
    # {% url %}/reverse() by name, not hardcoded paths, so this move is
    # transparent to the front end.
    path("refunds/pending/", refund_views.pending_refunds_view, name="pending-refunds"),
    path("refund/approve/<int:refund_id>/", refund_views.approve_refund_view, name="approve-refund"),
    path("refund/reject/<int:refund_id>/", refund_views.reject_refund_view, name="reject-refund"),

    path("razorpay/create-qr/<int:order_id>/", razorpay_views.create_razorpay_qr, name="razorpay-create-qr"),
    path("razorpay/qr-status/<str:qr_code_id>/", razorpay_views.razorpay_qr_status, name="razorpay-qr-status"),
    path("api/razorpay/webhook/", razorpay_views.razorpay_webhook, name="razorpay-webhook"),
]
