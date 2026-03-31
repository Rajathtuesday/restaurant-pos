# orders/services/order_lock_service.py
from django.utils import timezone
from datetime import timedelta
from orders.models import OrderLock


LOCK_DURATION_SECONDS = 30


def lock_order(order, user):
    """
    Lock an order for editing.

    Returns:
        (True, user)  -> lock acquired or refreshed
        (False, user) -> locked by another user
    """

    now = timezone.now()

    lock = getattr(order, "lock", None)

    # ---------------------------------
    # Existing lock
    # ---------------------------------

    if lock:

        # lock still valid and owned by another user
        if lock.expires_at > now and lock.locked_by != user:
            return False, lock.locked_by

        # lock expired OR same user -> refresh it
        lock.locked_by = user
        lock.expires_at = now + timedelta(seconds=LOCK_DURATION_SECONDS)
        lock.save(update_fields=["locked_by", "expires_at"])

        return True, user

    # ---------------------------------
    # No lock exists -> create one
    # ---------------------------------

    OrderLock.objects.create(
        order=order,
        locked_by=user,
        expires_at=now + timedelta(seconds=LOCK_DURATION_SECONDS)
    )

    return True, user