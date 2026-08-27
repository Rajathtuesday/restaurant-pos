# notifications/views.py
#
# unread_notifications used to live here, registered at
# api/notifications/unread/. orders.urls registers the exact same literal
# path earlier in core/urls.py's include order, so Django's resolver could
# never actually reach this view -- it was unreachable by construction, on
# top of nothing in the frontend ever calling it. The real, working
# endpoint for this data is orders.api.notification_api.
