from django.db import models

class GuestFeedback(models.Model):
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE)
    outlet = models.ForeignKey("tenants.Outlet", on_delete=models.CASCADE)
    order = models.ForeignKey("orders.Order", on_delete=models.SET_NULL, null=True, blank=True)
    
    guest_name = models.CharField(max_length=100, blank=True)
    rating = models.PositiveSmallIntegerField(help_text="1 to 5 stars")
    comment = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Feedback {self.rating}* by {self.guest_name or 'Anonymous'}"
