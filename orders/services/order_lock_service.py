# orders/services/order_lock_service.py
from django.db import transaction, IntegrityError
from django.utils import timezone
from datetime import timedelta
from orders.models import OrderLock


LOCK_DURATION_SECONDS = 300


def lock_order(order, user):
    """
    Lock an order for editing.

    Always issues a fresh SELECT FOR UPDATE against the database so that
    stale Python-object caches (from earlier prefetch_related / select_related
    calls) cannot cause two workers to simultaneously believe no lock exists.

    Returns:
        (True, user)  -> lock acquired or refreshed
        (False, user) -> locked by another user
    """

    now = timezone.now()

    # ---------------------------------
    # Always read from DB under a row lock to avoid TOCTOU.
    # getattr(order, "lock", None) is unsafe: the cached Python attribute
    # reflects the state at fetch time, not the current DB state.
    # ---------------------------------
    with transaction.atomic():
        lock = (
            OrderLock.objects
            .select_for_update()
            .filter(order=order)
            .select_related("locked_by")
            .first()
        )

        # ---------------------------------
        # Existing lock
        # ---------------------------------
        if lock:
            # lock still valid and owned by another user
            if lock.expires_at > now and lock.locked_by_id != user.pk:
                return False, lock.locked_by

            # lock expired OR same user -> refresh it
            lock.locked_by = user
            lock.expires_at = now + timedelta(seconds=LOCK_DURATION_SECONDS)
            lock.save(update_fields=["locked_by", "expires_at"])
            return True, user

        # ---------------------------------
        # No lock exists -> create one
        # ---------------------------------
        try:
            OrderLock.objects.create(
                order=order,
                locked_by=user,
                expires_at=now + timedelta(seconds=LOCK_DURATION_SECONDS)
            )
            return True, user
        except IntegrityError:
            # Another worker won the race between our SELECT and this INSERT.
            try:
                existing = OrderLock.objects.select_related("locked_by").get(order=order)
                return False, existing.locked_by
            except OrderLock.DoesNotExist:
                return True, user