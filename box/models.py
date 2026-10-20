"""The box's own record of itself: one row (pk 1), only used on a box."""
from django.db import models


class BoxState(models.Model):
    setup_done_at = models.DateTimeField(null=True, blank=True)

    # The latest time the box has seen. If the clock is ever behind it by
    # more than a few minutes, the clock went back (box/clock.py).
    last_seen_at = models.DateTimeField(null=True, blank=True)
    clock_warning = models.CharField(max_length=255, blank=True, default="")
    clock_warning_at = models.DateTimeField(null=True, blank=True)

    last_backup_at = models.DateTimeField(null=True, blank=True)
    last_backup_ok = models.BooleanField(null=True)
    last_backup_file = models.CharField(max_length=255, blank=True, default="")
    last_backup_bytes = models.BigIntegerField(null=True, blank=True)
    last_backup_error = models.CharField(max_length=500, blank=True, default="")

    # When this box first found a real licence made for another box (it
    # replaced a box that died, and that box's backup was restored here):
    # it keeps working for license.GRACE_DAYS from then (box/license.py).
    licence_moved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="box_state_is_one_row")]

    @classmethod
    def get(cls, lock=False):
        qs = cls.objects.select_for_update() if lock else cls.objects
        state = qs.filter(pk=1).first()
        if state is None:
            state, _ = cls.objects.get_or_create(pk=1)
        return state
