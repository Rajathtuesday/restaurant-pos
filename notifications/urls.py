# notifications/urls.py
#
# No routes here anymore -- unread_notifications was unreachable (shadowed
# by orders.urls registering the same literal path earlier in core/urls.py)
# and unused. See notifications/views.py.
urlpatterns = []
