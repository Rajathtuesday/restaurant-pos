from django.utils import timezone
from datetime import timedelta
from orders.models import OrderLock


def lock_order(order, user):

    now = timezone.now()

    lock = getattr(order, "lock", None)

    if lock and lock.expires_at > now and lock.locked_by != user:
        return False, lock.locked_by

    OrderLock.objects.update_or_create(
        order=order,
        defaults={
            "locked_by": user,
            "expires_at": now + timedelta(seconds=30)
        }
    )

    return True, None