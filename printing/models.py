# printing/models.py
from django.db import models

from core.models import TenantScopedModel


class PrintJob(TenantScopedModel):
    """
    Queued receipt/KOT print job consumed by the Rasova Agent in polling mode.

    The browser pushes a job here (HTTPS POST to EC2).
    The agent running on the local device polls /orders/agent/<key>/jobs/ every 2 s,
    prints to the local printer over TCP 9100, then marks the job done.

    This architecture means the agent never needs an open port or inbound connection —
    it only makes outbound HTTP calls, so Android battery optimisers and NAT routers
    cannot break the connection.

    Moved here from orders/models.py (Phase 1 of the orders app split) via a
    state-only migration -- the underlying table is still named orders_printjob
    (see Meta.db_table below), so this move touched zero rows and required
    zero downtime.
    """

    PENDING    = "pending"
    PROCESSING = "processing"
    DONE       = "done"
    FAILED     = "failed"
    STATUS_CHOICES = [
        (PENDING,    "Pending"),
        (PROCESSING, "Processing"),
        (DONE,       "Done"),
        (FAILED,     "Failed"),
    ]

    tenant     = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet     = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    # Full payload the agent needs: lines (ESC/POS), network_host, network_port, encoding
    payload    = models.JSONField()
    status     = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    done_at    = models.DateTimeField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    error_msg  = models.CharField(max_length=512, blank=True, default="")

    class Meta:
        db_table = "orders_printjob"
        ordering = ["created_at"]
        indexes  = [models.Index(fields=["tenant", "outlet", "status", "created_at"], name="orders_prin_tenant__e90431_idx")]

    def __str__(self):
        return f"PrintJob #{self.pk} [{self.status}] outlet={self.outlet_id}"

    # ── Redis poll-gating keys ────────────────────────────────────────────────
    # The agent poll endpoint checks a lightweight per-outlet Redis flag before
    # running the expensive claim transaction, so an idle poll costs one Redis
    # read instead of 2-3 Postgres queries.
    @staticmethod
    def pending_flag_key(outlet_id):
        return f"printq:pending:{outlet_id}"

    @staticmethod
    def sweep_key(outlet_id):
        return f"printq:swept:{outlet_id}"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        # Arm the per-outlet "has pending work" hint whenever a brand-new job is
        # queued as PENDING. Setting it AFTER super().save() means the row is
        # committed/visible before the flag is set — which is what makes the
        # poll's delete-before-read safe (no job is ever left with a cleared flag).
        # Over-arming is harmless (a false positive just costs one wasted poll).
        # A cache failure must never block creating a print job; the poll's
        # periodic safety sweep still delivers the job if the flag is lost.
        if is_new and self.status == self.PENDING:
            try:
                from django.core.cache import cache
                cache.set(self.pending_flag_key(self.outlet_id), 1, timeout=3600)
            except Exception:
                pass
