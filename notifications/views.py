# notifications/views.py
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from .models import Notification
from core.decorators import tenant_required

@login_required
@tenant_required
def unread_notifications(request):
    """
    Returns unread notifications for the current outlet.
    Marks them as read once fetched to prevent duplicate alerts.
    """
    # Fetch unread notifications
    notifications_qs = Notification.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_read=False
    ).order_by("-created_at")

    # Slice to get the latest 10
    notifications_list = list(notifications_qs[:10])

    data = []
    for n in notifications_list:
        data.append({
            "id": n.id,
            "type": n.type,
            "message": n.message,
            "created_at": n.created_at.isoformat()
        })
    
    # Mark only the fetched notifications as read
    if notifications_list:
        notif_ids = [n.id for n in notifications_list]
        Notification.objects.filter(id__in=notif_ids).update(is_read=True)

    return JsonResponse({"notifications": data})
